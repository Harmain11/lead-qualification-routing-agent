import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { getAccountProfile, updateAccountProfile } from "../api";
import type { AccountProfile, AccountProfileUpdate } from "../api";

type ProfileDraft = Omit<AccountProfileUpdate, "job_title" | "phone" | "bio"> & {
  job_title: string;
  phone: string;
  bio: string;
};

function toDraft(profile: AccountProfile): ProfileDraft {
  return {
    display_name: profile.display_name,
    job_title: profile.job_title ?? "",
    phone: profile.phone ?? "",
    bio: profile.bio ?? "",
    avatar_data_url: profile.avatar_data_url,
  };
}

async function preparePhoto(file: File): Promise<string> {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new Error("Choose a JPEG, PNG, or WebP image.");
  }
  if (file.size > 8 * 1024 * 1024) {
    throw new Error("Choose an image smaller than 8 MB.");
  }

  const bitmap = await createImageBitmap(file);
  try {
    const maxDimension = 512;
    const scale = Math.min(1, maxDimension / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("This browser cannot process the selected image.");
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
    if (dataUrl.length > 1_500_000) {
      throw new Error("The processed image is too large. Choose a smaller photo.");
    }
    return dataUrl;
  } finally {
    bitmap.close();
  }
}

export function AccountProfilePanel() {
  const [profile, setProfile] = useState<AccountProfile | null>(null);
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const photoInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    getAccountProfile()
      .then((result) => {
        if (!active) return;
        setProfile(result);
        setDraft(toDraft(result));
      })
      .catch((loadError) => {
        if (active) setError(loadError instanceof Error ? loadError.message : "Unable to load your account profile.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const update = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) => {
    setDraft((current) => current ? { ...current, [key]: value } : current);
    setNotice(null);
  };

  const onPhotoSelected = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    setNotice(null);
    try {
      update("avatar_data_url", await preparePhoto(file));
      setNotice("Photo selected. Save changes to keep it.");
    } catch (photoError) {
      setError(photoError instanceof Error ? photoError.message : "Unable to process this photo.");
    }
  };

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await updateAccountProfile({
        display_name: draft.display_name.trim(),
        job_title: draft.job_title.trim() || null,
        phone: draft.phone.trim() || null,
        bio: draft.bio.trim() || null,
        avatar_data_url: draft.avatar_data_url,
      });
      setProfile(saved);
      setDraft(toDraft(saved));
      setNotice("Account profile saved.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save your account profile.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <section className="panel account-profile-panel"><p className="account-profile-loading">Loading your account profile…</p></section>;
  }
  if (!draft || !profile) {
    return <section className="panel account-profile-panel"><h2>My account</h2><p className="account-profile-error" role="alert">{error || "Your account profile is unavailable."}</p></section>;
  }

  const initials = draft.display_name.trim().split(/\s+/).map((part) => part[0]).slice(0, 2).join("").toUpperCase() || "?";
  const unchanged = JSON.stringify(draft) === JSON.stringify(toDraft(profile));

  return (
    <section className="panel account-profile-panel" aria-labelledby="account-profile-title">
      <div className="account-profile-heading">
        <div className="account-profile-photo">
          {draft.avatar_data_url ? <img src={draft.avatar_data_url} alt="Your profile" /> : <span>{initials}</span>}
        </div>
        <div className="account-profile-summary">
          <h2 id="account-profile-title">My account</h2>
          <p>Personal details and profile photo for your Corral account.</p>
          <div className="account-profile-photo-actions">
            <input
              ref={photoInput}
              hidden
              type="file"
              accept="image/jpeg,image/png,image/webp"
              aria-label="Choose a profile photo"
              onChange={(event) => {
                void onPhotoSelected(event.currentTarget.files?.[0]);
                event.currentTarget.value = "";
              }}
            />
            <button className="secondary-button" type="button" onClick={() => photoInput.current?.click()} disabled={saving}>
              {draft.avatar_data_url ? "Change photo" : "Add photo"}
            </button>
            {draft.avatar_data_url && <button className="button-quiet" type="button" onClick={() => update("avatar_data_url", null)} disabled={saving}>Remove photo</button>}
          </div>
          <small className="account-profile-photo-help">JPEG, PNG, or WebP. Images are resized before saving.</small>
        </div>
      </div>

      <form className="account-profile-form" onSubmit={(event) => void save(event)}>
        <label>Full name
          <input required autoComplete="name" maxLength={255} value={draft.display_name} onChange={(event) => update("display_name", event.target.value)} />
        </label>
        <label>Email
          <input type="email" autoComplete="email" value={profile.email} disabled readOnly />
        </label>
        <label>Job title
          <input autoComplete="organization-title" maxLength={255} value={draft.job_title} onChange={(event) => update("job_title", event.target.value)} />
        </label>
        <label>Phone
          <input type="tel" autoComplete="tel" maxLength={64} value={draft.phone} onChange={(event) => update("phone", event.target.value)} />
        </label>
        <label className="account-profile-bio">Short bio
          <textarea maxLength={1000} rows={3} value={draft.bio} onChange={(event) => update("bio", event.target.value)} />
        </label>
        <div className="account-profile-form-actions">
          <button className="primary-button" type="submit" disabled={saving || !draft.display_name.trim() || unchanged}>
            {saving ? "Saving…" : "Save profile"}
          </button>
          {error && <span className="account-profile-error" role="alert">{error}</span>}
          {notice && <span className="account-profile-notice" role="status">{notice}</span>}
        </div>
      </form>
    </section>
  );
}