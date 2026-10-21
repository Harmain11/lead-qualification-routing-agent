import { useState } from "react";
import "./_group.css";

export function Current() {
  const [productsServices, setProductsServices] = useState(
    "Software / SaaS\nConsulting\nProfessional services",
  );

  return (
    <main className="business-profile-preview">
      <section className="business-profile-current">
        <p className="business-profile-current-copy">
          Help AI recognize your organization and its offerings when assessing
          incoming leads. Website suggestions stay editable; existing analyses
          are unchanged.
        </p>
        <form className="business-profile-current-form">
          <label>
            <span>Business name</span>
            <input placeholder="Your organization’s name" />
          </label>
          <label>
            <span>Other names or brands <small>One per line</small></span>
            <textarea rows={2} placeholder="Trading names, brands, or former names" />
          </label>
          <div className="business-profile-current-row">
            <div className="business-profile-current-website">
              <label>
                <span>Website</span>
                <input placeholder="https://example.com" />
              </label>
              <button type="button" disabled>Analyze website</button>
              <small className="business-profile-hint">
                Reads this page with your workspace AI provider. You review suggestions before saving.
              </small>
            </div>
            <label>
              <span>Business email domains <small>One per line</small></span>
              <textarea rows={2} placeholder="example.com" />
            </label>
          </div>
          <label>
            <span>What your business does</span>
            <textarea rows={2} placeholder="One short sentence about your organization." />
          </label>
          <label>
            <span>Products and services <small>One per line</small></span>
            <textarea
              rows={2}
              value={productsServices}
              onChange={(event) => setProductsServices(event.target.value)}
            />
          </label>
          <label>
            <span>How to distinguish your business from a lead</span>
            <textarea
              rows={2}
              placeholder="Add any important context about your organization and prospective customers."
            />
          </label>
          <div className="business-profile-current-actions">
            <button type="button" className="primary">Save business context</button>
          </div>
        </form>
      </section>
    </main>
  );
}
