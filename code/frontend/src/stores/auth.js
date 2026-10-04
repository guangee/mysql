import { defineStore } from 'pinia'
import { authApi } from '@/api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: JSON.parse(localStorage.getItem('user') || 'null'),
    accessToken: localStorage.getItem('access_token') || '',
  }),
  getters: {
    isLoggedIn: (state) => !!state.accessToken,
    isSuperuser: (state) => state.user?.is_superuser ?? false,
  },
  actions: {
    async login(username, password) {
      const { data } = await authApi.login({ username, password })
      this.accessToken = data.access
      this.user = data.user
      localStorage.setItem('access_token', data.access)
      localStorage.setItem('refresh_token', data.refresh)
      localStorage.setItem('user', JSON.stringify(data.user))
    },
    logout() {
      this.accessToken = ''
      this.user = null
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user')
    },
    async fetchMe() {
      const { data } = await authApi.me()
      this.user = data
      localStorage.setItem('user', JSON.stringify(data))
    },
  },
})
