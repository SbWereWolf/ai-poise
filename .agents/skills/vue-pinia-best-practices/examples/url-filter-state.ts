import { computed } from 'vue'
import type { LocationQuery, LocationQueryRaw, Router } from 'vue-router'

type QueryValue = LocationQuery[string] | undefined
function scalar(value: QueryValue): string | undefined {
  const first = Array.isArray(value) ? value[0] : value
  return typeof first === 'string' ? first : undefined
}
function positivePage(value: QueryValue): number {
  const raw = scalar(value)
  if (!raw || !/^[1-9]\d*$/.test(raw)) return 1
  const number = Number(raw)
  return Number.isSafeInteger(number) ? number : 1
}

// Call with the application's actual Router, not a separate router instance.
export function createUrlFilters(router: Router, history: 'push' | 'replace' = 'push') {
  const category = computed(() => scalar(router.currentRoute.value.query.category) || 'all')
  const search = computed(() => scalar(router.currentRoute.value.query.q) || '')
  const sort = computed(() => scalar(router.currentRoute.value.query.sort) || 'newest')
  const page = computed(() => positivePage(router.currentRoute.value.query.page))
  function navigate(patch: LocationQueryRaw) {
    const current = router.currentRoute.value
    return router[history]({
      path: current.path,
      hash: current.hash,
      query: { ...current.query, ...patch },
    })
  }
  function setCategory(value: string) {
    return navigate({ category: value === 'all' ? undefined : value, page: undefined })
  }
  function setSearch(value: string) {
    return navigate({ q: value || undefined, page: undefined })
  }
  function setSort(value: string) {
    return navigate({ sort: value === 'newest' ? undefined : value, page: undefined })
  }
  function setPage(value: number) {
    if (!Number.isSafeInteger(value) || value < 1) throw new RangeError('Page must be a positive safe integer')
    return navigate({ page: value === 1 ? undefined : String(value) })
  }
  return { category, search, sort, page, setCategory, setSearch, setSort, setPage }
}
