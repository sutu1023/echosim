<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { messagesFor } from '../i18n'
import { markLoggedOut } from '../session'
import type { Lang } from '../workbench/stage-links'
import { previousStageUrl } from '../environment/links'
import type {
  EnvironmentContextPayload,
  EnvironmentPreparationPayload,
} from '../environment/state'

const props = defineProps<{ projectId: string; lang: Lang }>()
const route = useRoute()
const router = useRouter()
const t = computed(() => messagesFor(props.lang).environment)
const loading = ref(true)
const errorText = ref('')
const preparation = ref<EnvironmentPreparationPayload | null>(null)

const preparationId = computed(() =>
  typeof route.query.preparation === 'string'
    ? route.query.preparation
    : null,
)
const buildId = computed(() =>
  typeof route.query.build === 'string' ? route.query.build : null,
)
const frozen = computed(() => preparation.value?.frozenSelection ?? null)
const prepareLink = computed(() =>
  previousStageUrl(props.projectId, route.query, buildId.value),
)

async function handleAuth(caught: unknown): Promise<boolean> {
  if (caught instanceof ApiError && caught.status === 401) {
    markLoggedOut()
    await router.replace({ name: 'login', query: { next: route.fullPath } })
    return true
  }
  return false
}

function apiBase(): string {
  return `/api/projects/${encodeURIComponent(props.projectId)}/environment`
}

async function load(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    let id = preparationId.value
    if (!id) {
      const suffix = buildId.value
        ? `?build=${encodeURIComponent(buildId.value)}`
        : ''
      const context = await api.get<EnvironmentContextPayload>(
        `${apiBase()}${suffix}`,
      )
      id = context.preparation?.preparationId ?? null
    }
    if (!id) {
      preparation.value = null
      return
    }
    const data = await api.get<{ preparation: EnvironmentPreparationPayload }>(
      `${apiBase()}/preparations/${encodeURIComponent(id)}`,
    )
    preparation.value = data.preparation
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    errorText.value =
      caught instanceof ApiError && caught.status === 503
        ? t.value.serviceUnavailable
        : t.value.loadFailed
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="run-panel card" data-view="dual-platform-run">
    <p v-if="loading" class="muted" aria-live="polite">{{ t.loading }}</p>
    <div v-else-if="errorText" class="error-block" role="alert">
      <p>{{ errorText }}</p>
      <button type="button" class="primary" @click="load">{{ t.retry }}</button>
    </div>
    <template v-else-if="frozen">
      <p class="eyebrow">{{ t.frozenSelection }}</p>
      <h2>{{ t.runPendingTitle }}</h2>
      <p class="muted">{{ t.runPendingBody }}</p>
      <dl class="run-summary">
        <dt>{{ t.frozenQuestion }}</dt>
        <dd>{{ frozen.question }}</dd>
        <dt>{{ t.frozenRounds }}</dt>
        <dd>{{ frozen.settings.rounds }}</dd>
        <dt>{{ t.frozenRoles }}</dt>
        <dd>{{ frozen.roles.length }}</dd>
        <dt>{{ t.platforms }}</dt>
        <dd>
          {{ frozen.platforms.map((item) => `${item.platformKey} → ${item.nativePlatform}`).join(' · ') }}
        </dd>
      </dl>
    </template>
    <template v-else>
      <h2>{{ t.runPendingMissing }}</h2>
      <RouterLink class="button-link" :to="prepareLink">
        {{ t.goPrepare }}
      </RouterLink>
    </template>
  </section>
</template>

<style scoped>
.run-panel {
  display: grid;
  gap: 0.75rem;
}
.run-summary {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.35rem 1rem;
}
.run-summary dt {
  font-weight: 600;
}
.run-summary dd {
  margin: 0;
}
</style>
