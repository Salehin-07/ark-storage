/* Settings dialog: every option the server supports, grouped, with one consistent save bar. */
(function () {
  "use strict";
  const { h, icon, toast, modal, ask, api, fmtBytes } = window.UI;

  async function open(startTab, hooks) {
    hooks = hooks || {};
    let settings, status;
    try {
      [settings, status] = await Promise.all([api("/api/settings"), api("/api/status")]);
    } catch (e) { toast(e.message, "bad"); return; }
    let S = settings.settings;
    const drafts = {};
    const body = h("div", { style: { minHeight: "300px" } });
    const saveBtn = h("button", { class: "btn primary", type: "button", disabled: true, onclick: save }, "Save changes");
    const dirtyNote = h("span", { class: "muted", style: { marginRight: "auto" } });
    const footExtra = h("div", { style: { display: "flex", alignItems: "center", gap: "10px", flex: 1, minWidth: "180px" } }, dirtyNote, saveBtn);
    let tab = startTab || (status.problems && status.problems.length ? "repo" : "repo");

    const TABS = [["repo", "Repository"], ["enc", "Encryption"], ["perf", "Performance"], ["beh", "Behavior"], ["acct", "Account"], ["maint", "Maintenance"], ["about", "About"]];
    const nav = h("div", { class: "set-nav", role: "tablist" });
    const val = (k) => (k in drafts ? drafts[k] : S[k].value);
    const setDraft = (k, v) => {
      const orig = S[k].secret ? "" : S[k].value;
      if (v === orig && !S[k].secret) delete drafts[k]; else if (S[k].secret && v === "") delete drafts[k]; else drafts[k] = v;
      const n = Object.keys(drafts).length;
      saveBtn.disabled = !n;
      dirtyNote.textContent = n ? `${n} unsaved change${n > 1 ? "s" : ""}` : "";
    };

    const src = (k) => h("span", { class: "badge", style: { marginLeft: "6px", verticalAlign: "middle" } }, S[k].source === "settings" ? "saved here" : S[k].source === "env" ? "from environment" : "default");
    function row(k, label, hint, control, full) {
      return h("div", { class: "sr" },
        h("div", { class: "lbl" }, label, k ? src(k) : null, hint ? h("small", null, hint) : null),
        h("div", { class: "ctl" + (full ? " full" : "") }, control));
    }
    const text = (k, o) => { o = o || {}; const i = h("input", { class: "inp", type: o.type || "text", value: val(k) || "", placeholder: o.placeholder || "", spellcheck: false, autocapitalize: "none", autocomplete: "off", oninput: () => setDraft(k, i.value) }); return i; };
    const num = (k) => { const i = h("input", { class: "inp", type: "number", min: S[k].min, max: S[k].max, value: val(k), inputMode: "numeric", oninput: () => setDraft(k, i.value === "" ? "" : Number(i.value)) }); return i; };
    const sw = (k) => { const i = h("input", { type: "checkbox", checked: !!val(k), role: "switch", onchange: () => setDraft(k, i.checked) }); return h("label", { class: "switch" }, i, h("span", { class: "track" })); };
    const seg = (k) => { const w = h("div", { class: "seg" }); const draw = () => { w.replaceChildren(...S[k].choices.map((c) => h("button", { type: "button", class: val(k) === c ? "on" : "", onclick: () => { setDraft(k, c); draw(); } }, c[0].toUpperCase() + c.slice(1)))); }; draw(); return w; };
    const secret = (k, ph) => {
      const i = h("input", { class: "inp", type: "password", autocomplete: "new-password", placeholder: S[k].set ? "•••••••• (saved — type to replace)" : ph, oninput: () => setDraft(k, i.value) });
      return i;
    };

    function card(title, hint, ...rows) { return h("section", { class: "card" }, h("h3", null, title), hint ? h("div", { class: "hint" }, hint) : null, ...rows); }
    const btn = (label, fn, cls, ic) => { const b = h("button", { class: "btn " + (cls || ""), type: "button", onclick: async () => { b.disabled = true; try { await fn(); } catch (e) { toast(e.message, "bad"); } finally { b.disabled = false; } } }, ic ? icon(ic) : null, label); return b; };

    const views = {
      repo() {
        const result = h("div", { class: "hint", style: { marginTop: "8px" } });
        const warn = status.private === false ? h("div", { class: "card", style: { borderColor: "var(--warn)" } }, h("b", null, "This repository is public. "), "Anyone on the internet can read plain (unencrypted) files stored in it. Make it private on GitHub, or turn on encryption.") : null;
        return h("div", null, warn,
          card("GitHub repository", "Only these two are required. They normally come from the ARK_REPO_URL and ARK_GITHUB_TOKEN environment variables; anything you enter here wins over the environment.",
            row("repo_url", "Repository", "owner/repo or the full URL", text("repo_url", { placeholder: "https://github.com/you/your-storage" })),
            row("token", "Access token", "A fine-grained token with Contents: read & write on this one repository.", secret("token", "github_pat_…")),
            row("branch", "Storage branch", "Files live on their own branch so they never mix with code. It is created automatically.", text("branch", { placeholder: "ark-data" })),
            h("div", { style: { marginTop: "10px" } }, btn("Save & test connection", async () => { await save(); const r = await api("/api/settings/test", { method: "POST", body: {} }); result.textContent = `${r.message} (${r.repo}, ${r.private ? "private" : "PUBLIC"}${r.can_push ? "" : ", read-only token!"})`; result.style.color = r.can_push ? "var(--ok)" : "var(--danger)"; if (hooks.reload) hooks.reload(); }, "", "refresh"), result)),
          card("Commit identity", "Name and email recorded on commits made by Ark Storage.",
            row("commit_name", "Name", "", text("commit_name")), row("commit_email", "Email", "", text("commit_email"))));
      },
      enc() {
        const c = status.crypto || {};
        const st = h("div", { class: "kv" }, h("dt", null, "New uploads"), h("dd", null, c.enabled ? "encrypted" : "stored as plain files"),
          h("dt", null, "Passphrase"), h("dd", null, c.passphrase_set ? "set" : "not set"), h("dt", null, "Repository"), h("dd", null, c.initialised ? "encryption initialised" : "not initialised yet"));
        return h("div", null,
          card("Encryption", "AES-256-GCM, applied before anything leaves this server. GitHub only ever sees ciphertext. File and folder names stay visible; contents and sizes of pieces do not.",
            row("encrypt", "Encrypt new files", "Existing files are not touched unless you convert them below.", sw("encrypt")),
            row("passphrase", "Passphrase", "Write-only: never shown again. Lose it and encrypted files cannot be recovered — nobody can reset it, including GitHub.", secret("passphrase", "Choose a long passphrase")),
            h("div", { style: { marginTop: "12px" } }, st)),
          card("Convert existing files", "Runs in the background, in batches. Old versions remain in the repository's git history until you compact it (Maintenance).",
            h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
              btn("Encrypt all files", async () => { if (await ask("Encrypt every file?", "All plain files will be re-stored encrypted. This can take a while for large libraries.", { ok: "Encrypt all" })) { await api("/api/admin/convert", { method: "POST", body: { encrypted: true } }); toast("Started — follow it in Transfers"); hooks.jobs && hooks.jobs(); } }, "", "lock"),
              btn("Decrypt all files", async () => { if (await ask("Decrypt every file?", "All files will be stored as plain data in the repository" + (status.private === false ? " — and this repository is PUBLIC." : "."), { ok: "Decrypt all", danger: true })) { await api("/api/admin/convert", { method: "POST", body: { encrypted: false } }); toast("Started — follow it in Transfers"); hooks.jobs && hooks.jobs(); } }, "danger"),
              btn("Reset encryption setup", async () => { if (await ask("Reset encryption setup?", "Only possible when no file is encrypted. Lets you pick a new passphrase.", { ok: "Reset" })) { await api("/api/admin/reset-crypto", { method: "POST", body: {} }); toast("Encryption setup reset"); } }, "ghost")))); 
      },
      perf() {
        return card("Performance & limits", "Defaults suit most setups. Pieces must stay under GitHub's 100 MB per-file limit.",
          row("chunk_mb", "Piece size (MB)", "Files larger than this are split into pieces and joined again on download. 1–95.", num("chunk_mb")),
          row("cache_mb", "Download cache (MB)", "Recently opened pieces are kept on this server's disk so repeat views are instant.", num("cache_mb")),
          row("max_upload_mb", "Upload limit per file (MB)", "0 = no limit. The server needs about twice a file's size in free disk while it uploads.", num("max_upload_mb")),
          row("refresh_seconds", "Re-check GitHub every (s)", "How long a folder listing is trusted before asking GitHub for changes. 0 = every time.", num("refresh_seconds")));
      },
      beh() {
        return card("Behavior", null,
          row("trash", "Use the trash", "Deleted items can be restored. Turn off to delete permanently right away.", sw("trash")),
          row("show_hidden", "Show hidden files", "Names that start with a dot.", sw("show_hidden")),
          row("share_hours", "Share links last (hours)", "Default lifetime of a link made with Share.", num("share_hours")),
          row("theme", "Theme", "", seg("theme")),
          row("session_hours", "Stay signed in (hours)", "Applies after the server restarts.", num("session_hours")));
      },
      acct() {
        const cur = h("input", { class: "inp", type: "password", autocomplete: "current-password" });
        const nu = h("input", { class: "inp", autocomplete: "username", placeholder: "Leave empty to keep", autocapitalize: "none" });
        const np = h("input", { class: "inp", type: "password", autocomplete: "new-password" });
        const np2 = h("input", { class: "inp", type: "password", autocomplete: "new-password" });
        return card("Sign-in", "The default is ark / ark. Change it before exposing this server to the internet.",
          row(null, "Current password", "", cur), row(null, "New username", "", nu), row(null, "New password", "", np), row(null, "Repeat new password", "", np2),
          h("div", { style: { marginTop: "10px" } }, btn("Update account", async () => {
            if (np.value && np.value !== np2.value) throw new Error("The new passwords don't match");
            if (!np.value && !nu.value.trim()) throw new Error("Enter a new username or password");
            const r = await api("/api/account", { method: "POST", body: { current: cur.value, username: nu.value.trim(), password: np.value } });
            toast("Account updated"); cur.value = np.value = np2.value = nu.value = ""; if (hooks.account) hooks.account(r.user);
          }, "primary")));
      },
      maint() {
        const s = status.stats || {};
        return h("div", null,
          card("Storage", null, h("dl", { class: "kv" },
            h("dt", null, "Files"), h("dd", null, String(s.files ?? "—")), h("dt", null, "Folders"), h("dd", null, String(s.folders ?? "—")),
            h("dt", null, "Your data"), h("dd", null, s.bytes != null ? fmtBytes(s.bytes) : "—"), h("dt", null, "Repository size"), h("dd", null, status.repo_kb != null ? fmtBytes(status.repo_kb * 1024) + " (GitHub recommends staying under ~5 GB)" : "—"),
            h("dt", null, "Cache on disk"), h("dd", null, fmtBytes(status.cache_bytes || 0)), h("dt", null, "Branch"), h("dd", { class: "mono" }, status.branch))),
          card("Maintenance", "Safe to run any time.", h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
            btn("Refresh from GitHub", async () => { await api("/api/admin/refresh", { method: "POST", body: {} }); toast("Refreshed"); hooks.reload && hooks.reload(); }, "", "refresh"),
            btn("Clear cache", async () => { await api("/api/admin/clear-cache", { method: "POST", body: {} }); toast("Cache cleared"); }, ""),
            btn("Compact history", async () => {
              if (await ask("Compact history?", "Replaces the branch history with one commit holding your current files, so deleted data stops counting against the repository's size. Your files are unaffected. History (old versions) is lost.", { ok: "Compact" })) { await api("/api/admin/squash", { method: "POST", body: {} }); toast("Started — follow it in Transfers"); hooks.jobs && hooks.jobs(); }
            }, "danger"))));
      },
      about() {
        return card("Ark Storage " + status.version, "Your own cloud storage on top of a GitHub repository. Open source, MIT licensed.",
          h("dl", { class: "kv" }, h("dt", null, "Repository"), h("dd", null, status.repo || status.repo_url || "not configured"), h("dt", null, "Visibility"), h("dd", null, status.private === undefined ? "—" : status.private ? "private" : "public"),
            h("dt", null, "git on server"), h("dd", null, status.git ? "available" : "MISSING — uploads need it")),
          h("p", { class: "hint" }, "Source and docs: ", h("a", { href: "https://github.com/Salehin-07/ark-storage", target: "_blank", rel: "noopener" }, "github.com/Salehin-07/ark-storage")));
      },
    };

    function render() {
      nav.replaceChildren(...TABS.map(([id, label]) => h("button", { type: "button", role: "tab", class: tab === id ? "on" : "", onclick: () => { tab = id; render(); } }, label)));
      body.replaceChildren(views[tab]());
    }

    async function save() {
      if (!Object.keys(drafts).length) return;
      const changes = {};
      for (const [k, v] of Object.entries(drafts)) changes[k] = v;
      try {
        const r = await api("/api/settings", { method: "PUT", body: { changes } });
        S = r.settings;
        for (const k of Object.keys(drafts)) delete drafts[k];
        saveBtn.disabled = true; dirtyNote.textContent = "";
        toast("Settings saved");
        status = await api("/api/status");
        render();
        if (hooks.saved) hooks.saved(S, changes);
      } catch (e) { toast(e.message, "bad"); throw e; }
    }

    const dlg = await modal({ title: "Settings", wide: true, nav, body, footExtra, buttons: [{ label: "Close", value: true }],
      ready: () => render() });
    return dlg;
  }

  window.Settings = { open };
})();
