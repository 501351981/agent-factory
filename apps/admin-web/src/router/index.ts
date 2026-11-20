import { createRouter, createWebHistory } from 'vue-router'
import AgentEditView from '../views/AgentEditView.vue'
import AgentsView from '../views/AgentsView.vue'
import LoginView from '../views/LoginView.vue'
import MemoriesView from '../views/MemoriesView.vue'
import ModelsView from '../views/ModelsView.vue'
import PlatformSkillsView from '../views/PlatformSkillsView.vue'
import SchedulesView from '../views/SchedulesView.vue'
import SessionsView from '../views/SessionsView.vue'
import TeamsView from '../views/TeamsView.vue'
import UsageView from '../views/UsageView.vue'
import { isLoggedIn } from '../auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'agents', component: AgentsView },
    { path: '/agents/:id', name: 'agent-edit', component: AgentEditView },
    { path: '/teams', name: 'teams', component: TeamsView },
    { path: '/platform-skills', name: 'platform-skills', component: PlatformSkillsView },
    { path: '/models', name: 'models', component: ModelsView },
    { path: '/usage', name: 'usage', component: UsageView },
    { path: '/sessions', name: 'sessions', component: SessionsView },
    { path: '/schedules', name: 'schedules', component: SchedulesView },
    { path: '/memories', name: 'memories', component: MemoriesView },
  ],
})

router.beforeEach((to) => {
  const loggedIn = isLoggedIn()
  const redirect = safeRedirect(to.query.redirect)
  if (to.name === 'login') {
    return loggedIn ? { path: redirect } : true
  }
  if (!loggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  return true
})

/** 只接受站内绝对路径，避免开放重定向。 */
function safeRedirect(value: unknown): string {
  const raw = typeof value === 'string' ? value : ''
  return raw.startsWith('/') && !raw.startsWith('//') ? raw : '/'
}
