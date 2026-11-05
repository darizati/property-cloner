import axios from "axios";

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "",
});

export const fetchDiff = (payload) => client.post("/api/properties/fetch-diff", payload);

export const createProfile = (payload) => client.post("/api/profiles", payload);
export const listProfiles = () => client.get("/api/profiles");
export const getProfile = (id) => client.get(`/api/profiles/${id}`);
export const updateProfile = (id, payload) => client.put(`/api/profiles/${id}`, payload);
export const deleteProfile = (id) => client.delete(`/api/profiles/${id}`);
export const runProfile = (id) => client.post(`/api/profiles/${id}/run`);
export const updateSchedule = (id, schedule_cron) =>
  client.put(`/api/profiles/${id}/schedule`, { schedule_cron });

export const listJobs = (profileId) =>
  client.get("/api/jobs", { params: profileId ? { profile_id: profileId } : {} });
export const getJob = (id) => client.get(`/api/jobs/${id}`);

export default client;
