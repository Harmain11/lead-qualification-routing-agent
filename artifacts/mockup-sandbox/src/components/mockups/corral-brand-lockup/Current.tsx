import "./_group.css";

export function Current() {
  return (
    <main className="corral-brand-preview">
      <div className="corral-brand-scale">
        <div className="rail-brand-row">
          <button className="brand-lockup" type="button" aria-label="Corral home">
            <img src="/__mockup/images/corral-mark.png" alt="" />
            <span>
              <b>Corral</b>
              <small className="rail-brand-tagline">
                <span>Powered by</span>
                <span>Ingenuity Systems</span>
              </small>
            </span>
          </button>
          <button className="rail-close" type="button" aria-label="Collapse navigation">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m14.5 5-5 7 5 7" />
            </svg>
          </button>
        </div>
      </div>
    </main>
  );
}
