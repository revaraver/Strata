// Included inside the --serve loop; all runtime/session objects are in scope.
#ifdef _WIN32
            if (line.rfind("MSAVE ", 0) == 0 || line.rfind("MRESTORE ", 0) == 0) {
                const bool saving = line[1] == 'S';
                const std::string name = line.substr(saving ? 6 : 9);
                const auto t0 = Clock::now();
                uint64_t total_bytes = 0, total_tokens = 0, count = 0;
                auto wait_memory = [&](const char* phase, uint64_t bytes) {
                    const auto seconds = strata::core::session_phase_limit_s(bytes);
                    strata::core::progress_at(phase); strata::core::progress_allow(seconds);
                    std::printf("SWAIT %s %lld\n", phase, (long long) seconds); std::fflush(stdout);
                };
                try {
                    const auto ns = strata::platform::winmem::space();
                    if (ns.empty() || name.rfind(ns + "-s-", 0) != 0 || !stages.empty() || multi_gpu || o.peer_device >= 1)
                        throw std::runtime_error("RAM sessions require the Windows memory namespace and one GPU");
                    if (batch_on()) throw std::runtime_error("a parallel request is still active");
                    if (!ver.wait_commit(err) || !mtp.idle(err)) throw std::runtime_error(err);
                    strata::core::SessionFileIdentity id;
                    wait_memory("fingerprint", 2u << 20);
                    if (!session_identity(id, err, [&] { wait_memory("fingerprint", 2u << 20); })) throw std::runtime_error(err);
                    auto manifest = strata::platform::winmem::region(name, 4096, "session", saving);
                    auto* header = (strata::platform::winmem::Header*) manifest->data;
                    if (saving) {
                        if (!manifest->fresh) throw std::runtime_error("RAM session name already used");
                        *header = {strata::platform::winmem::magic, 0, id.model, 0};
                        auto write_image = [&](const strata::core::SavedConversation& image,
                                               const std::vector<strata::core::SessionKvSource>* sources) {
                            wait_memory("capture", image.bytes());
                            strata::core::SessionWriteOptions opt;
                            opt.min_free_bytes = 0; opt.durable = false;
                            // One session at a time, 16 MiB transfers. The allowance is renewed by actual blocks.
                            opt.progress = [&](uint64_t done, uint64_t) { wait_memory("memory", done); };
                            const std::string path = "mem:" + name + "-i" + std::to_string(count);
                            size_t bytes = 0;
                            const bool ok = sources ? strata::core::session_file_write(path, image, *sources, id, bytes, err, opt)
                                                    : strata::core::session_file_write(path, image, id, bytes, err, opt);
                            if (!ok) throw std::runtime_error(err);
                            total_bytes += bytes; total_tokens += image.live.ids.size(); ++count;
                        };
                        // Existing parked images are moved one at a time. Serialized chunks replace their old
                        // storage before the next image is captured; never duplicate the entire conversation bank.
                        const size_t parked = conversations.size();
                        for (size_t i = 0; i < parked; ++i) {
                            auto image = conversations.take(0);
                            try { write_image(image, nullptr); }
                            catch (...) { conversations.put(std::move(image)); throw; }
                        }
                        auto capture = [&](const strata::core::ConversationView& view,
                                           strata::core::SessionState& state, auto& draft) {
                            strata::core::SavedConversation meta;
                            std::vector<strata::core::SessionKvSource> sources;
                            if (!strata::core::conversation_snapshot_sources(meta, sources, view, state, g, draft, err))
                                throw std::runtime_error(err);
                            write_image(meta, &sources);
                        };
                        bool live_in_slot = false;
                        for (size_t b = 0; b < bs.size(); ++b) {
                            auto& sl = bs[b];
                            if (!sl.cached || sl.ids.empty() || sl.tainted) continue;
                            if (!batch_mtp) throw std::runtime_error("RAM batch sessions require --batch-mtp");
                            if (!slot_mtp[b]->idle(err)) throw std::runtime_error(err);
                            const strata::core::ConversationView view{sl.ids, {}, sl.checks, sl.cvec};
                            capture(view, *bslot_ss[0][b], slot_mtp[b]->kv_state());
                            if (live.size() <= sl.ids.size() && std::equal(live.begin(), live.end(), sl.ids.begin()))
                                live_in_slot = true;
                        }
                        if (live_ok && !live.empty() && !live_in_slot) {
                            const strata::core::ConversationView view{live, live_imgs, checks, cvec_cached};
                            capture(view, ss, mtp.kv_state());
                        }
                        header->bytes = count; MemoryBarrier(); header->ready = 1;
                    } else {
                        if (header->magic != strata::platform::winmem::magic || header->ready != 1 ||
                            header->identity != id.model || header->bytes > 1024)
                            throw std::runtime_error("incompatible/incomplete RAM session manifest");
                        count = header->bytes;
                        for (uint64_t i = 0; i < count; ++i) {
                            wait_memory("restore", total_bytes);
                            strata::core::SessionReadLimits limits;
                            if (!strata::core::conversation_session_read_limits(limits, ss, g, mtp.kv_state(),
                                    (uint64_t) o.max_context, (uint64_t) std::max(o.prompt_cache, 1), err))
                                throw std::runtime_error(err);
                            limits.progress = [&](uint64_t done, uint64_t) { wait_memory("memory", done); };
                            strata::core::SavedConversation image;
                            size_t bytes = 0;
                            const std::string part = name + "-i" + std::to_string(i);
                            if (!strata::core::session_file_read("mem:" + part, id, image, bytes, err, limits))
                                throw std::runtime_error(err);
                            if (!strata::core::conversation_snapshot_validate(image, ss, g, mtp.kv_state(), err))
                                throw std::runtime_error(err);
                            total_tokens += image.live.ids.size(); total_bytes += bytes;
                            // These are the already resident sessions from before unloading, not new conversations.
                            // Preserve them even when the configured parking budget excludes idle GPU slots.
                            conversations.restore_parked(std::move(image));
                            strata::platform::winmem::forget(part);
                            std::printf("MEMDROP %s\n", part.c_str()); std::fflush(stdout);
                        }
                        strata::platform::winmem::forget(name);
                        std::printf("MEMDROP %s\n", name.c_str()); std::fflush(stdout);
                    }
                    const double ms = std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
                    std::fprintf(stderr, "strata serve: RAM %s %llu conversations, %llu tokens, %llu bytes in %.1f ms (no disk)\n",
                                 saving ? "saved" : "restored", (unsigned long long) count,
                                 (unsigned long long) total_tokens, (unsigned long long) total_bytes, ms);
                    std::printf("%s %llu %llu %.1f\n", saving ? "SAVED" : "RESTORED",
                                (unsigned long long) total_tokens, (unsigned long long) total_bytes, ms);
                } catch (const std::exception& e) {
                    std::string why = e.what();
                    for (char& c : why) if (c == '\r' || c == '\n') c = ' ';
                    std::printf("SERR memory 0 %s\n", why.c_str());
                }
                std::fflush(stdout);
                continue;
            }
#endif
