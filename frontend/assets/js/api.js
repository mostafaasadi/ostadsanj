const API_BASE = "http://localhost:8000";

const api = {
  async get(endpoint) {
    const res = await fetch(`${API_BASE}${endpoint}`);
    if (!res.ok) throw new Error(`API Error: ${res.status}`);
    return res.json();
  },

  async post(endpoint, data) {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || "API Error");
    }
    return res.json();
  },

  async put(endpoint, data) {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error("API Error");
    return res.json();
  },

  async delete(endpoint) {
    const res = await fetch(`${API_BASE}${endpoint}`, { method: "DELETE" });
    if (!res.ok) throw new Error("API Error");
    return res.json();
  },

  async patch(endpoint, data) {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error("API Error");
    return res.json();
  },

  getOverview() { return this.get("/api/overview"); },
  getRecentMessages(limit = 15) { return this.get(`/api/messages/recent?limit=${limit}`); },
  getProfessors() { return this.get("/api/professors/with-stats"); },
  getProfessor(professorId) { return this.get(`/api/professors/${professorId}`); },
  createProfessorsBulk(professors) { return this.post("/api/professors/bulk", { professors }); },
  deleteProfessor(id) { return this.delete(`/api/professors/${id}`); },
  runMatching() { return this.post("/api/professors/match", {}); },
  resetMatching(mode = "unmatched") { return this.post("/api/professors/reset-matching", { mode }); },
  getProfessorMessages(professorId, page = 1, limit = 50, method = "all") { return this.get(`/api/professors/${professorId}/messages?page=${page}&limit=${limit}&method=${method}`); },
  reviewMatch(messageId, professorId, approved) { return this.patch(`/api/matches/${messageId}/${professorId}`, { approved }); },
  getAnalysisStats() { return this.get("/api/analysis/stats"); },
  runAnalysis(batchSize, continuous = false, workers = 3, provider = "arvan", model = null) {
    return this.post("/api/analysis/run", { batch_size: batchSize, continuous: continuous, workers: workers, provider: provider, model: model });
  },
  getLogs(limit = 100) { return this.get(`/api/analysis/logs?limit=${limit}`); },
  resetAnalysis(mode = "failed") { return this.post("/api/analysis/reset", { mode }); },
  getJobs() { return this.get("/api/jobs"); },
  getProfessorsRanking() {
    return this.get("/api/professors/ranking");
  },
  getProfessorProfile(professorId) {
    return this.get(`/api/professors/${professorId}/profile`);
  },
  getProfessorScore(professorId) {
    return this.get(`/api/professors/${professorId}/score`);
  },
  importLocal(path) { return this.post("/api/import/local", { path }); }
};