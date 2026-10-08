const faNumber = new Intl.NumberFormat("fa-IR");

const ui = {
  showAlert(message, type = "info", container = null) {
    const alertArea = container || document.getElementById("alertArea");
    if (!alertArea) return;
    
    const icons = {
      success: "fa-check-circle",
      danger: "fa-exclamation-circle",
      warning: "fa-triangle-exclamation",
      info: "fa-info-circle"
    };
    
    alertArea.innerHTML = `
      <div class="alert alert-${type}">
        <i class="fas ${icons[type]}"></i>
        <span>${message}</span>
      </div>
    `;
    
    setTimeout(() => {
      alertArea.innerHTML = "";
    }, 5000);
  },

  statCard(title, value, icon, color) {
    return `
      <div class="card">
        <div class="stat-card">
          <div class="stat-icon ${color}">
            <i class="fas ${icon}"></i>
          </div>
          <div>
            <div class="stat-value">${value}</div>
            <div class="stat-label">${title}</div>
          </div>
        </div>
      </div>
    `;
  },

  badge(text, type) {
    return `<span class="badge-${type}">${text}</span>`;
  },

  statusBadge(status) {
    const map = {
      "pending": ["در انتظار", "badge-pending"],
      "running": ["در حال اجرا", "badge-running"],
      "done": ["انجام شد", "badge-done"],
      "error": ["خطا", "badge-error"]
    };
    const [text, cls] = map[status] || [status, "badge-pending"];
    return `<span class="badge ${cls}">${text}</span>`;
  },

  emptyState(icon, title, description) {
    return `
      <div class="empty-state">
        <i class="fas ${icon}"></i>
        <h5>${title}</h5>
        <p>${description}</p>
      </div>
    `;
  },

  modal(title, content, footer = "") {
    return `
      <div class="modal-overlay active" id="modalOverlay">
        <div class="modal-content">
          <div class="modal-header">
            <h5>${title}</h5>
            <button class="close-btn" onclick="ui.closeModal()">&times;</button>
          </div>
          <div class="modal-body">${content}</div>
          ${footer ? `<div class="modal-footer">${footer}</div>` : ""}
        </div>
      </div>
    `;
  },

  closeModal() {
    const overlay = document.getElementById("modalOverlay");
    if (overlay) overlay.remove();
  }
};