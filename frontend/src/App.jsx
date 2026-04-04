import Lenis from "lenis";
import { startTransition, useEffect, useMemo, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const ACTIVE_JOB_STORAGE_KEY = "ai-video-gen-active-job";
const VIEW_HASH_GENERATOR = "#generator";
const VIEW_HASH_GALLERY = "#gallery";
const ACTIVE_STATUSES = new Set(["queued", "preparing", "rendering", "finalizing"]);

async function fetchJson(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        detail = payload.detail;
      }
    } catch {
      // Keep fallback detail.
    }
    throw new Error(detail);
  }
  return response.json();
}

function formatPercent(value) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function formatDate(value) {
  if (!value) {
    return "";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function statusCopy(job) {
  if (!job) {
    return "";
  }
  if (job.status === "queued") {
    return job.message || "Waiting for the current render slot.";
  }
  return "Smooth progress is live. Rendering continues in the background even if this tab closes.";
}

function outputUrl(path) {
  if (!path) {
    return "";
  }
  return `/assets/outputs/${path.split("/").at(-1)}`;
}

function readStoredJobId() {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = window.localStorage.getItem(ACTIVE_JOB_STORAGE_KEY);
  const parsed = raw ? Number(raw) : NaN;
  return Number.isInteger(parsed) ? parsed : null;
}

function writeStoredJobId(jobId) {
  if (typeof window === "undefined") {
    return;
  }
  if (jobId == null) {
    window.localStorage.removeItem(ACTIVE_JOB_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, String(jobId));
}

function currentViewFromHash() {
  if (typeof window === "undefined") {
    return "generator";
  }
  return window.location.hash === VIEW_HASH_GALLERY ? "gallery" : "generator";
}

function setViewHash(view) {
  if (typeof window === "undefined") {
    return;
  }
  const nextHash = view === "gallery" ? VIEW_HASH_GALLERY : VIEW_HASH_GENERATOR;
  if (window.location.hash !== nextHash) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${nextHash}`);
  }
}

function trimCopy(value, maxLength) {
  const normalized = (value || "").replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 3).trimEnd()}...`;
}

function videoTitle(video) {
  return trimCopy(video?.title || video?.quote || "", 80) || "Generated video";
}

function videoSubtitle(video) {
  const author = trimCopy(video?.author || "", 32);
  return author ? author : formatDate(video?.created_at);
}

export default function App() {
  const [view, setView] = useState(currentViewFromHash);
  const [job, setJob] = useState(null);
  const [overview, setOverview] = useState(null);
  const [videos, setVideos] = useState([]);
  const [galleryLoaded, setGalleryLoaded] = useState(false);
  const [previewVideo, setPreviewVideo] = useState(null);
  const [hoveredVideoName, setHoveredVideoName] = useState(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const lenisRef = useRef(null);

  const active = useMemo(() => job && ACTIVE_STATUSES.has(job.status), [job]);
  const videoUrl = useMemo(() => outputUrl(job?.output_path), [job?.output_path]);

  async function refreshJob(jobId) {
    const nextJob = await fetchJson(`/api/jobs/${jobId}`);
    startTransition(() => {
      setJob(nextJob);
    });
    return nextJob;
  }

  async function refreshOverview() {
    const nextOverview = await fetchJson("/api/library/overview");
    startTransition(() => {
      setOverview(nextOverview);
    });
    return nextOverview;
  }

  async function refreshActiveJobFallback() {
    const jobs = await fetchJson("/api/jobs");
    const nextActiveJob = jobs.find((candidate) => ACTIVE_STATUSES.has(candidate.status));
    if (!nextActiveJob) {
      return null;
    }
    startTransition(() => {
      setJob(nextActiveJob);
    });
    return nextActiveJob;
  }

  async function refreshVideos() {
    const nextVideos = await fetchJson("/api/library/videos");
    startTransition(() => {
      setVideos(nextVideos);
      setGalleryLoaded(true);
    });
    return nextVideos;
  }

  useEffect(() => {
    function handleHashChange() {
      setView(currentViewFromHash());
    }

    refreshOverview().catch(() => {});
    const storedJobId = readStoredJobId();
    const tasks = [];
    if (storedJobId) {
      tasks.push(
        refreshJob(storedJobId).catch(() => {
          writeStoredJobId(null);
        }),
      );
    } else {
      tasks.push(
        refreshActiveJobFallback().catch(() => null),
      );
    }

    Promise.allSettled(tasks).finally(() => {
      setHydrated(true);
    });

    window.addEventListener("hashchange", handleHashChange);
    return () => {
      window.removeEventListener("hashchange", handleHashChange);
    };
  }, []);

  useEffect(() => {
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    if (reduceMotion) {
      return undefined;
    }
    const lenis = new Lenis({
      duration: 1.05,
      smoothWheel: true,
      wheelMultiplier: 0.92,
      touchMultiplier: 1,
    });
    lenisRef.current = lenis;

    let frameId = 0;
    function raf(time) {
      lenis.raf(time);
      frameId = window.requestAnimationFrame(raf);
    }

    frameId = window.requestAnimationFrame(raf);
    return () => {
      window.cancelAnimationFrame(frameId);
      lenis.destroy();
      lenisRef.current = null;
    };
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      refreshOverview().catch(() => {});
      if (view === "gallery" && galleryLoaded) {
        refreshVideos().catch(() => {});
      }
      if (!job || !ACTIVE_STATUSES.has(job.status)) {
        refreshActiveJobFallback().catch(() => null);
      }
    }, 15000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [galleryLoaded, job, view]);

  useEffect(() => {
    writeStoredJobId(active ? job.id : null);
  }, [active, job]);

  useEffect(() => {
    if (!active || !job?.id) {
      return undefined;
    }

    const source = new EventSource(`${API_BASE}/api/jobs/${job.id}/stream`);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      setJob((current) => ({
        ...current,
        status: payload.status,
        progress: payload.progress,
        phase: payload.phase,
        message: payload.message,
        updated_at: payload.created_at,
      }));
    };
    source.addEventListener("end", () => {
      source.close();
      Promise.all([refreshJob(job.id), refreshOverview(), refreshVideos().catch(() => [])]).catch((error) => {
        setErrorMessage(error.message);
      });
    });
    source.onerror = () => {
      source.close();
      refreshJob(job.id).catch((error) => {
        setErrorMessage(error.message);
      });
    };
    return () => {
      source.close();
    };
  }, [active, job?.id]);

  useEffect(() => {
    if (!previewVideo) {
      document.body.style.overflow = "";
      document.documentElement.style.overflow = "";
      return undefined;
    }
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    function handleEscape(event) {
      if (event.key === "Escape") {
        setPreviewVideo(null);
      }
    }
    window.addEventListener("keydown", handleEscape);
    return () => {
      document.body.style.overflow = "";
      document.documentElement.style.overflow = "";
      window.removeEventListener("keydown", handleEscape);
    };
  }, [previewVideo]);

  async function startGeneration() {
    setIsCreating(true);
    setErrorMessage("");
    try {
      const created = await fetchJson("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const nextJob = Array.isArray(created) ? created[0] : created;
      if (!nextJob?.id) {
        throw new Error("Generation did not return a job");
      }
      startTransition(() => {
        setJob(nextJob);
        setOverview((current) => current ? {
          ...current,
          jobs: {
            ...current.jobs,
            active: current.jobs.active + 1,
          },
        } : current);
      });
      refreshOverview().catch(() => {});
      setViewHash("generator");
      setView("generator");
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setIsCreating(false);
    }
  }

  async function handleGenerateAnother() {
    writeStoredJobId(null);
    setJob(null);
    await startGeneration();
  }

  function openGallery() {
    setViewHash("gallery");
    setView("gallery");
    if (!galleryLoaded) {
      refreshVideos().catch((error) => {
        setErrorMessage(error.message);
      });
    }
  }

  function openGenerator() {
    setViewHash("generator");
    setView("generator");
  }

  function openPreview(video) {
    setPreviewVideo(video);
  }

  function closePreview() {
    setPreviewVideo(null);
  }

  if (!hydrated) {
    return <div className="loading-screen">Loading studio...</div>;
  }

  return (
    <>
      <main className="page-shell">
        <div className="glow glow-left" />
        <div className="glow glow-right" />

        {view === "gallery" ? (
          <section className="gallery-shell">
            <div className="gallery-header">
              <div>
                <p className="eyebrow">Video Archive</p>
                <h1>Every completed video, cleaner and faster to browse.</h1>
                <p className="supporting-text">
                  Only finished renders appear here. Hover a card for a lightweight motion preview, or open the full portrait player instantly.
                </p>
              </div>
              <div className="gallery-header-actions">
                <button className="secondary-cta" type="button" onClick={openGenerator}>
                  Back to Generator
                </button>
              </div>
            </div>

            {videos.length ? (
              <section className="video-grid" aria-label="Completed videos">
                {videos.map((video) => {
                  const isHovered = hoveredVideoName === video.name;
                  return (
                    <article
                      key={video.name}
                      className={`video-card${isHovered ? " is-active" : ""}`}
                      onMouseEnter={() => setHoveredVideoName(video.name)}
                      onMouseLeave={() => setHoveredVideoName(null)}
                    >
                      <button
                        className="video-card-hitbox"
                        type="button"
                        onClick={() => openPreview(video)}
                        onFocus={() => setHoveredVideoName(video.name)}
                        onBlur={() => setHoveredVideoName(null)}
                        aria-label={`Preview ${videoTitle(video)}`}
                      >
                        <div className="video-card-player">
                          {isHovered ? (
                            <video
                              key={video.name}
                              src={video.url}
                              muted
                              autoPlay
                              loop
                              playsInline
                              preload="metadata"
                            />
                          ) : (
                            <div className="video-card-poster">
                              <span className="poster-badge">Ready</span>
                              <strong>{trimCopy(videoTitle(video), 56)}</strong>
                              <p>{trimCopy(video.quote || "", 112) || "Motivational portrait render."}</p>
                            </div>
                          )}
                        </div>
                        <div className="video-card-body">
                          <strong>{videoTitle(video)}</strong>
                          <span>{videoSubtitle(video)}</span>
                        </div>
                      </button>
                      <div className="video-card-actions">
                        <button className="primary-ghost small-cta" type="button" onClick={() => openPreview(video)}>
                          Preview
                        </button>
                        <a className="secondary-cta small-cta" href={video.url} download>
                          Download
                        </a>
                      </div>
                    </article>
                  );
                })}
              </section>
            ) : (
              <section className="state-card empty-gallery">
                <h2>No completed videos yet.</h2>
                <p className="supporting-text narrow">
                  Finished renders will appear here automatically after generation completes.
                </p>
                <button className="primary-cta" type="button" onClick={openGenerator}>
                  Generate Video
                </button>
              </section>
            )}

            {errorMessage && <p className="global-error">{errorMessage}</p>}
          </section>
        ) : (
          <section className="hero-card">
            <div className="hero-copy">
              <p className="eyebrow">Video Forge</p>
              <h1>Generate a beautiful motivational video with one click.</h1>
              <p className="supporting-text">
                Your video keeps rendering on the server even if you leave the page. Come back any time
                to watch and download it.
              </p>
              <div className="status-strip" aria-label="Generation status">
                <StatusChip label="In Progress" value={overview?.jobs?.active ?? 0} />
                <StatusChip label="Generated" value={overview?.jobs?.completed ?? 0} />
                <StatusChip label="Failed" value={overview?.jobs?.failed ?? 0} />
              </div>
            </div>

            {!job && (
              <section className="state-card idle-state">
                <div className="orbital-wrap" aria-hidden="true">
                  <span className="orbital-ring ring-one" />
                  <span className="orbital-ring ring-two" />
                  <span className="orbital-core" />
                </div>
                <div className="hero-actions">
                  <button className="primary-cta" type="button" disabled={isCreating} onClick={startGeneration}>
                    {isCreating ? "Starting render..." : "Generate Video"}
                  </button>
                  <button className="secondary-cta" type="button" onClick={openGallery}>
                    View All Videos
                  </button>
                </div>
              </section>
            )}

            {active && job && (
              <section className="state-card progress-state">
                <div className="status-head">
                  <span className="phase-pill">{job.phase}</span>
                  <span className="phase-meta">Job #{job.id}</span>
                </div>
                <h2>{job.message || "Rendering your video"}</h2>
                <p className="supporting-text narrow">{statusCopy(job)}</p>

                <div className="progress-track" aria-label={`Progress ${formatPercent(job.progress)}`}>
                  <div className="progress-fill" style={{ width: formatPercent(job.progress) }} />
                </div>

                <div className="progress-meta">
                  <strong>{formatPercent(job.progress)}</strong>
                  <span>{job.phase}</span>
                </div>

                <div className="signal-row" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                  <span />
                </div>

                <div className="result-actions progress-actions">
                  <button className="secondary-cta" type="button" onClick={openGallery}>
                    View All Videos
                  </button>
                </div>
              </section>
            )}

            {job?.status === "completed" && (
              <section className="state-card result-state">
                <div className="status-head">
                  <span className="phase-pill success">Ready</span>
                  <span className="phase-meta">{formatDate(job.completed_at || job.updated_at)}</span>
                </div>
                <div className="player-shell">
                  <video src={videoUrl} controls playsInline preload="metadata" />
                </div>
                <div className="result-actions">
                  <a className="primary-cta" href={videoUrl} download>
                    Download Video
                  </a>
                  <button className="secondary-cta" type="button" onClick={() => openPreview({
                    url: videoUrl,
                    title: job.quote,
                    quote: job.quote,
                    author: job.author,
                    created_at: job.completed_at || job.updated_at,
                  })}
                  >
                    Preview
                  </button>
                  <button className="secondary-cta" type="button" onClick={openGallery}>
                    View All Videos
                  </button>
                  <button className="secondary-cta" type="button" onClick={handleGenerateAnother}>
                    Generate Another
                  </button>
                </div>
              </section>
            )}

            {job?.status === "failed" && (
              <section className="state-card failure-state">
                <div className="status-head">
                  <span className="phase-pill danger">Failed</span>
                  <span className="phase-meta">Job #{job.id}</span>
                </div>
                <h2>Rendering hit a problem.</h2>
                <p className="error-copy">{job.error || job.message || "Unknown render error"}</p>
                <div className="result-actions">
                  <button className="primary-cta" type="button" onClick={handleGenerateAnother}>
                    Try Again
                  </button>
                  <button className="secondary-cta" type="button" onClick={openGallery}>
                    View All Videos
                  </button>
                </div>
              </section>
            )}

            {job?.status === "cancelled" && (
              <section className="state-card failure-state">
                <div className="status-head">
                  <span className="phase-pill">Cancelled</span>
                </div>
                <h2>Generation was cancelled.</h2>
                <div className="result-actions">
                  <button className="primary-cta" type="button" onClick={handleGenerateAnother}>
                    Generate Again
                  </button>
                  <button className="secondary-cta" type="button" onClick={openGallery}>
                    View All Videos
                  </button>
                </div>
              </section>
            )}

            {errorMessage && <p className="global-error">{errorMessage}</p>}
          </section>
        )}
      </main>

      {previewVideo && (
        <div className="lightbox" role="dialog" aria-modal="true" aria-label={videoTitle(previewVideo)}>
          <button className="lightbox-backdrop" type="button" onClick={closePreview} aria-label="Close preview" />
          <div className="lightbox-panel" data-lenis-prevent>
            <button className="lightbox-close" type="button" onClick={closePreview} aria-label="Close preview">
              Close
            </button>
            <div className="lightbox-layout">
              <div className="lightbox-player-shell">
                <video
                  key={previewVideo.url}
                  src={previewVideo.url}
                  controls
                  autoPlay
                  playsInline
                  preload="metadata"
                />
              </div>
              <div className="lightbox-copy">
                <p className="eyebrow">Preview</p>
                <h2>{videoTitle(previewVideo)}</h2>
                <p className="supporting-text narrow">
                  {trimCopy(previewVideo.quote || previewVideo.title || "", 180)}
                </p>
                <p className="preview-meta">
                  {previewVideo.author ? `${previewVideo.author} · ` : ""}{formatDate(previewVideo.created_at)}
                </p>
                <div className="result-actions modal-actions">
                  <a className="primary-cta" href={previewVideo.url} download>
                    Download Video
                  </a>
                  <button className="secondary-cta" type="button" onClick={closePreview}>
                    Back to Gallery
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function StatusChip({ label, value }) {
  return (
    <div className="status-chip">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
