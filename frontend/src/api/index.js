import http from './http'

export const authApi = {
  login: (data) => http.post('/auth/login/', data),
  me: () => http.get('/auth/me/'),
}

export const dashboardApi = {
  get: (hours) => http.get('/dashboard/', { params: hours ? { hours } : {} }),
}

export const databaseApi = {
  list: () => http.get('/databases/'),
  create: (data) => http.post('/databases/', data),
  detail: (name) => http.get(`/databases/${encodeURIComponent(name)}/`),
  tableStructure: (name, table) =>
    http.get(`/databases/${encodeURIComponent(name)}/tables/${encodeURIComponent(table)}/`),
  query: (name, data) => http.post(`/databases/${encodeURIComponent(name)}/query/`, data),
  export: (name, data) =>
    http.post(`/databases/${encodeURIComponent(name)}/export/`, data, {
      responseType: 'blob',
      timeout: 3600000,
    }),
}

export const mysqlApi = {
  settings: () => http.get('/mysql/settings/'),
  updateSettings: (data) => http.patch('/mysql/settings/', data),
}

export const accountApi = {
  businessList: () => http.get('/accounts/business/'),
  createBusiness: (data) => http.post('/accounts/business/', data),
  systemList: () => http.get('/accounts/system/'),
  changeBusinessPassword: (user, host, data) =>
    http.post(`/accounts/business/${encodeURIComponent(user)}/${encodeURIComponent(host)}/password/`, data),
  changeSystemPassword: (role, data) =>
    http.post(`/accounts/system/${role}/password/`, data),
}

export const backupApi = {
  jobs: (limit = 50) => http.get('/backups/jobs/', { params: { limit } }),
  trigger: (type) => http.post(`/backups/jobs/trigger/${type}/`),
  files: (refresh = false) => http.get('/backups/files/', { params: refresh ? { refresh: 1 } : {} }),
  upload: (formData, onUploadProgress) =>
    http.post('/backups/upload/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 3600000,
      onUploadProgress,
    }),
  restoreTrigger: (data) => http.post('/backups/restore/trigger/', data),
  restoreJobs: (limit = 20) => http.get('/backups/restore/jobs/', { params: { limit } }),
  retention: () => http.get('/backups/retention/'),
  updateRetention: (data) => http.patch('/backups/retention/', data),
  cleanupJobs: (limit = 20) => http.get('/backups/cleanup/jobs/', { params: { limit } }),
  triggerCleanup: (scope) => http.post('/backups/cleanup/trigger/', { scope }),
  pitrOptions: (refresh = false) => http.get('/backups/pitr/options/', { params: refresh ? { refresh: 1 } : {} }),
  pitrPreview: (data) => http.post('/backups/pitr/preview/', data),
  pitrJobs: (limit = 20) => http.get('/backups/pitr/jobs/', { params: { limit } }),
  pitrTrigger: (data) => http.post('/backups/pitr/trigger/', data),
  dbPitrPreview: (data) => http.post('/backups/pitr/database/preview/', data),
  dbPitrJobs: (limit = 20) => http.get('/backups/pitr/database/jobs/', { params: { limit } }),
  dbPitrTrigger: (data) => http.post('/backups/pitr/database/trigger/', data),
  download: (storageId, key) =>
    http.get('/backups/download/', { params: { storage_id: storageId, key } }),
  downloadProxy: (storageId, key) =>
    http.get('/backups/download/proxy/', {
      params: { storage_id: storageId, key },
      responseType: 'blob',
      timeout: 3600000,
    }),
  log: (lines = 300) => http.get('/backups/log/', { params: { lines } }),
}

export const storageApi = {
  list: () => http.get('/storages/'),
  create: (data) => http.post('/storages/', data),
  update: (id, data) => http.patch(`/storages/${id}/`, data),
  remove: (id) => http.delete(`/storages/${id}/`),
  test: (id) => http.post(`/storages/${id}/test/`),
  importEnv: () => http.post('/storages/import-env/'),
}
