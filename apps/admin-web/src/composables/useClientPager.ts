import { computed, reactive, watch, type ComputedRef, type Ref } from 'vue'

export type ClientPager = {
  page: number
  pageSize: number
  total: number
}

export function useClientPager<T>(
  rows: Ref<T[]> | ComputedRef<T[]>,
  defaultPageSize = 20,
) {
  const pager = reactive<ClientPager>({
    page: 1,
    pageSize: defaultPageSize,
    total: 0,
  })

  watch(
    rows,
    (list) => {
      pager.total = list.length
      const maxPage = Math.max(1, Math.ceil(pager.total / pager.pageSize) || 1)
      if (pager.page > maxPage) pager.page = maxPage
    },
    { immediate: true },
  )

  const pagedRows = computed(() => {
    const start = (pager.page - 1) * pager.pageSize
    return rows.value.slice(start, start + pager.pageSize)
  })

  function onPageSizeChange() {
    pager.page = 1
  }

  return { pager, pagedRows, onPageSizeChange }
}
