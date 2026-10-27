type PreviewMode = "current" | "repaired";

export function TeamPerformanceExcerpt({ mode }: { mode: PreviewMode }) {
  return (
    <main className={`performance-excerpt-stage ${mode}`}>
      <section className="v-card v-card-pad t-compare" aria-labelledby="t-compare-title">
        <header className="t-board-head">
          <div>
            <h2 id="t-compare-title" className="v-card-title">Compare side by side</h2>
            <p className="v-card-sub">Pick two or three people from the leaderboard.</p>
          </div>
        </header>
        <div className="t-compare-pick">
          <button type="button" className="t-chip" aria-pressed="false">
            <span className="t-avatar" aria-hidden="true">JP</span>
            Junaid Paracha
          </button>
        </div>
      </section>

      <section className="t-notes" aria-label="Member performance summary">
        <article className="v-card v-card-pad">
          <b>Junaid Paracha</b>
          <p className="v-card-sub">
            Junaid Paracha has 3 assigned lead(s) in this period, 3 qualified lead(s), and 0 win(s): 0 Project(s), 0 Sale(s). 3 remain open and 0 were lost. Assigned-lead conversion is 0%.
          </p>
        </article>
      </section>

      <p className="t-foot">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="5.5" />
          <path d="M8 7v3M8 5.2h.01" />
        </svg>
        <span>Revenue, wins, response times and deal records are shared across this workspace.</span>
      </p>
    </main>
  );
}
