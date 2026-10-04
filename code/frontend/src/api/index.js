import http from './http'

export const authApi = {
  login: (data) => http.post('/auth/login/', data),
  me: () => http.get('/auth/me/'),
}

export const systemApi = {
  health: () => http.get('/healthz/'),
}

export const dashboardApi = {
  get: (hours) => http.get('/dashboard/', { params: hours ? { hours } : {} }),
}

export const databaseApi = {
  list: () => http.get('/databases/'),
  create: (data) => http.post('/databases/', data),
  detail: (name, refresh = false) =>
    http.get(`/databases/${encodeURIComponent(name)}/`, { params: refresh ? { refresh: 1 } : {} }),
  tableStructure: (name, table, refresh = false) =>
    http.get(`/databases/${encodeURIComponent(name)}/tables/${encodeURIComponent(table)}/`, {
      params: refresh ? { refresh: 1 } : {},
    }),
  schemaChanges: (name, params = {}) =>
    http.get(`/databases/${encodeURIComponent(name)}/schema-changes/`, { params }),
  metrics: (name, hours) =>
    http.get(`/databases/${encodeURIComponent(name)}/metrics/`, { params: hours ? { hours } : {} }),
  query: (name, data) => http.post(`/databases/${encodeURIComponent(name)}/query/`, data),
  export: (name, data) =>
    http.post(`/databases/${encodeURIComponent(name)}/export/`, data, {
      responseType: 'blob',
      timeout: 3600000,
    }),
}

export const mysqlApi = {
  settings: (params = {}) => http.get('/mysql/settings/', { params }),
  updateSettings: (data) => http.patch('/mysql/settings/', data),
  serviceStatus: () => http.get('/mysql/service/'),
  controlService: (data) => http.post('/mysql/service/', data, { timeout: 240000 }),
}

export const dtsApi = {
  databases: () => http.get('/dts/databases/'),
  connections: () => http.get('/dts/connections/'),
  createConnection: (data) => http.post('/dts/connections/', data),
  updateConnection: (id, data) => http.patch(`/dts/connections/${id}/`, data),
  removeConnection: (id) => http.delete(`/dts/connections/${id}/`),
  testSavedConnection: (id) => http.post(`/dts/connections/${id}/test/`),
  connectionDatabases: (id) => http.get(`/dts/connections/${id}/databases/`),
  createRemoteDatabase: (id, data) => http.post(`/dts/connections/${id}/databases/`, data),
  tasks: () => http.get('/dts/tasks/'),
  createTask: (data) => http.post('/dts/tasks/', data),
  updateTask: (id, data) => http.patch(`/dts/tasks/${id}/`, data),
  removeTask: (id) => http.delete(`/dts/tasks/${id}/`),
  testConnection: (data) => http.post('/dts/test-connection/', data),
  detail: (id) => http.get(`/dts/tasks/${id}/`),
  sqlEvents: (id, limit = 200) => http.get(`/dts/tasks/${id}/sql-events/`, { params: { limit } }),
  action: (id, action, data = {}) => http.post(`/dts/tasks/${id}/${action}/`, data),
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
  jobs: ({ page = 1, pageSize = 10 } = {}) =>
    http.get('/backups/jobs/', { params: { page, page_size: pageSize } }),
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
