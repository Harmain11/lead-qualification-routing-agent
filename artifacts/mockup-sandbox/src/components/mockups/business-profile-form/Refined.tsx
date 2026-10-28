import { useState } from "react";
import "./_group.css";

const servicePresets = [
  "Software / SaaS",
  "Consulting",
  "Professional services",
  "Managed services",
  "Marketing and advertising",
  "Design and creative services",
  "Training and education",
  "Financial services",
  "Healthcare services",
  "Logistics and fulfillment",
  "Manufacturing",
  "Hardware and equipment",
  "Retail and e-commerce",
  "Consumer products",
  "Subscription services",
];

export function Refined() {
  const [productsServices, setProductsServices] = useState(
    "Software / SaaS\nConsulting\nProfessional services",
  );
  const [preset, setPreset] = useState("");

  function addPreset(value: string) {
    if (!value) return;
    const existing = productsServices.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    if (!existing.some((item) => item.toLowerCase() === value.toLowerCase())) {
      setProductsServices([...existing, value].join("\n"));
    }
    setPreset("");
  }

  return (
    <main className="business-profile-preview">
      <section className="business-profile-refined">
        <header className="business-profile-refined-header">
          <span className="business-profile-eyebrow">Workspace settings</span>
          <h1>Business profile</h1>
          <p>
            Help AI recognize your organization and separate it from prospective
            leads. Review website suggestions before saving.
          </p>
        </header>
        <form className="business-profile-refined-form">
          <section className="business-profile-section">
            <div className="business-profile-section-heading">
              <span className="business-profile-section-number">1</span>
              <div>
                <h2>Organization</h2>
                <p>Names and domains that identify your business.</p>
              </div>
            </div>
            <div className="business-profile-field-grid">
              <label>
                <span>Business name</span>
                <input defaultValue="Northstar Systems" />
              </label>
              <label>
                <span>Other names or brands <small>One per line</small></span>
                <textarea rows={2} placeholder="Trading names or product brands" />
              </label>
            </div>
          </section>

          <section className="business-profile-section">
            <div className="business-profile-section-heading">
              <span className="business-profile-section-number">2</span>
              <div>
                <h2>Website and email</h2>
                <p>Analyze your public website to get cited profile suggestions.</p>
              </div>
            </div>
            <div className="business-profile-field-grid">
              <label>
                <span>Website</span>
                <div className="business-profile-website-input">
                  <input defaultValue="https://northstar.example" />
                  <button type="button">Analyze website</button>
                </div>
                <small className="business-profile-help">
                  Only this page is read; suggestions are not saved automatically.
                </small>
              </label>
              <label>
                <span>Business email domains <small>One per line</small></span>
                <textarea rows={2} placeholder="company.com" />
              </label>
            </div>
          </section>

          <section className="business-profile-section">
            <div className="business-profile-section-heading">
              <span className="business-profile-section-number">3</span>
              <div>
                <h2>What you offer</h2>
                <p>Short details help AI recognize your organization in incoming mail.</p>
              </div>
            </div>
            <div className="business-profile-field-grid">
              <label>
                <span>What your business does</span>
                <textarea
                  rows={3}
                  defaultValue="We help growing teams automate customer outreach."
                />
              </label>
              <label>
                <span>Products and services <small>One per line</small></span>
                <textarea
                  rows={3}
                  value={productsServices}
                  onChange={(event) => setProductsServices(event.target.value)}
                />
                <div className="business-profile-service-controls">
                  <select
                    aria-label="Add a common product or service"
                    value={preset}
                    onChange={(event) => addPreset(event.target.value)}
                  >
                    <option value="">Add a common option…</option>
                    {servicePresets.map((item) => <option key={item}>{item}</option>)}
                  </select>
                  <small>Select an option to add it to your list, or type your own.</small>
                </div>
              </label>
            </div>
          </section>

          <section className="business-profile-section">
            <div className="business-profile-section-heading">
              <span className="business-profile-section-number">4</span>
              <div>
                <h2>Lead context <small>Optional</small></h2>
                <p>Add a note if your business is easy to confuse with a prospective customer.</p>
              </div>
            </div>
            <div className="business-profile-field-grid">
              <label className="business-profile-full-field">
                <span>How to distinguish your business from a lead</span>
                <textarea
                  rows={2}
                  placeholder="For example, we provide services to clinics; we are not a clinic."
                />
              </label>
            </div>
          </section>

          <div className="business-profile-save-row">
            <small>Suggestions stay editable and are applied only when you save.</small>
            <button type="button" className="primary">Save business context</button>
          </div>
        </form>
      </section>
    </main>
  );
}
