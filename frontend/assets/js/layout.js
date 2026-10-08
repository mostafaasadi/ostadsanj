const layout = {
  menuItems: [
    { section: "اصلی" },
    { title: "پیشخان", icon: "fa-chart-pie", href: "index.html", id: "dashboard" },
    { title: "ایمپورت و آماده‌سازی", icon: "fa-upload", href: "import.html", id: "import" },
    { section: "مدیریت و تحلیل" },
    { title: "اساتید و رتبه‌بندی", icon: "fa-users", href: "professors.html", id: "professors" },
    { title: "تحلیل هوشمند", icon: "fa-brain", href: "analysis.html", id: "analysis" },
    { section: "گزارش‌ها" },
    { title: "پیام‌ها و تطبیق‌ها", icon: "fa-comments", href: "messages.html", id: "messages" }
  ],

  stylesInjected: false,

  injectStyles() {
    if (this.stylesInjected) return;
    const style = document.createElement("style");
    style.textContent = `
      .sidebar { display: flex; flex-direction: column; }
      .sidebar-nav { flex: 1; }
      .sidebar-footer {
        margin-top: auto; padding: 1rem 1.2rem;
        border-top: 1px solid rgba(255,255,255,.08);
        display: flex; flex-direction: column; gap: .5rem;
      }
      .sf-chip {
        display: flex; align-items: center; gap: .5rem;
        font-size: .74rem; color: rgba(255,255,255,.75);
        background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.1);
        border-radius: .6rem; padding: .4rem .7rem;
      }
      .sf-chip b { color: #fff; }
    `;
    document.head.appendChild(style);
    this.stylesInjected = true;
  },

  render(activePage) {
    const menuHtml = this.menuItems.map(item => {
      if (item.section) {
        return `<li class="nav-section">${item.section}</li>`;
      }
      const isActive = item.id === activePage;
      const disabled = item.disabled ? 'style="opacity:0.5;pointer-events:none"' : "";
      return `
        <li>
          <a href="${item.href}" class="${isActive ? "active" : ""}" ${disabled}>
            <i class="fas ${item.icon}"></i>
            <span>${item.title}</span>
          </a>
        </li>`;
    }).join("");

    return `
      <aside class="sidebar">
        <div class="sidebar-brand">
          <h1><i class="fas fa-graduation-cap"></i> استادسنج</h1>
          <p>سامانه تحلیل نظرات دانشجویان</p>
        </div>
        <ul class="sidebar-nav">${menuHtml}</ul>
        <div class="sidebar-footer">
          <span class="sf-chip"><i class="fas fa-message"></i> <b id="sfMessages">-</b> پیام در سامانه</span>
          <span class="sf-chip"><i class="fas fa-brain"></i> <b id="sfAnalyzed">-</b> تحلیل ثبت‌شده</span>
        </div>
      </aside>
      <main class="main-content">
        <div id="pageContent"></div>
      </main>
    `;
  },

  init(activePage) {
    this.injectStyles();
    const app = document.getElementById("app");
    if (!app) {
      console.error("Element #app not found");
      return document.body;
    }
    app.innerHTML = `<div class="layout">${this.render(activePage)}</div>`;
    this.loadSidebarStats();
    return document.getElementById("pageContent");
  },

  fmt(n) {
    try {
      if (typeof faNumber !== "undefined" && faNumber && faNumber.format) return faNumber.format(n);
    } catch (e) { /* silent */ }
    return String(n);
  },

  async loadSidebarStats() {
    try {
      if (typeof api === "undefined") return;
      const [ov, st] = await Promise.all([api.getOverview(), api.getAnalysisStats()]);
      const m = document.getElementById("sfMessages");
      const a = document.getElementById("sfAnalyzed");
      if (m) m.textContent = this.fmt(ov.counts.total_messages);
      if (a) a.textContent = this.fmt(st.total_analyzed);
    } catch (e) { /* silent */ }
  }
};