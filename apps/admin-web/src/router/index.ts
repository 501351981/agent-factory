import { createRouter, createWebHistory } from 'vue-router'
import AgentEditView from '../views/AgentEditView.vue'
import AgentsView from '../views/AgentsView.vue'
import MemoriesView from '../views/MemoriesView.vue'
import ModelsView from '../views/ModelsView.vue'
import PlatformSkillsView from '../views/PlatformSkillsView.vue'
import SessionsView from '../views/SessionsView.vue'
import UsageView from '../views/UsageView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'agents', component: AgentsView },
    { path: '/agents/:id', name: 'agent-edit', component: AgentEditView },
    { path: '/platform-skills', name: 'platform-skills', component: PlatformSkillsView },
    { path: '/models', name: 'models', component: ModelsView },
    { path: '/usage', name: 'usage', component: UsageView },
    { path: '/sessions', name: 'sessions', component: SessionsView },
    { path: '/memories', name: 'memories', component: MemoriesView },
  ],
})
