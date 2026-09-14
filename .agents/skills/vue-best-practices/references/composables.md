---
title: Composable Organization Patterns
impact: MEDIUM
impactDescription: Well-structured composables improve maintainability, reusability, and update performance
type: best-practice
tags: [vue3, composables, composition-api, code-organization, api-design, readonly, utilities]
---

# Composable Organization Patterns

**Impact: MEDIUM** - Treat composables as reusable, stateful building
blocks and keep their code organized by feature concern. This keeps
large components maintainable and prevents hard-to-debug mutation and
API design issues.

## Task List

- Compose complex behavior from small, focused composables
- Use options objects for composables with multiple optional parameters
- Return readonly state when updates must flow through explicit actions
- Keep pure utility functions as plain utilities, not composables
- Organize composable and component code by feature concern, and extract
  composables when components grow
- Accept plain values, refs, computed refs, or getters only through an
  explicit adaptable-input contract
- Keep writable ownership and callback/getter semantics unambiguous
- Test adaptable inputs, reactive updates, cleanup, errors, and
  component unmount

## Adapt Values, Refs, and Getters Without Hiding Ownership

Use an adaptable input only when the same reusable composable genuinely
needs to accept a plain value, `Ref<T>`, `ComputedRef<T>`, or getter. Do
not widen every composable API pre-emptively.

### Read-only inputs

- Use Vue's `MaybeRefOrGetter<T>` for a read-only adaptable input.
- Resolve it with `toValue()` inside `computed`, `watch`, or
  `watchEffect`
  when ref/getter dependencies must remain reactive. Resolving once
  outside a reactive context loses future updates.
- Return refs/computed values and explicit commands. Do not expose a
  mutable internal reactive object without an ownership contract.

```typescript
import { computed, toValue, type MaybeRefOrGetter } from 'vue'

export function useNormalizedCode(source: MaybeRefOrGetter<string>) {
  const normalizedCode = computed(() => toValue(source).trim().toUpperCase())

  return { normalizedCode }
}
```

### Writable inputs

- A plain value is never an implicit two-way binding.
- For writable ownership, require an explicit writable `Ref<T>`,
  writable computed, or setter/update callback.
- Name the update operation so consumers can see who owns mutation.

### Functions that are data

A callback, predicate, comparator, factory, or command is not
automatically a getter input. Type function-valued data separately so
`toValue()` cannot invoke it by accident. When a composable accepts both
a getter and a callback, give them separate named parameters or distinct
branded/structured types.

### Lifecycle and cleanup

Register lifecycle-aware side effects synchronously. Clean up watchers,
event listeners, timers, sockets, observers, and pending requests. Keep
framework-independent business rules outside the composable when they do
not need Vue reactivity or lifecycle.

### Required tests

Test the same contract with:

1. a plain value;
2. a `ref`;
3. a computed ref or getter;
4. source updates after setup;
5. readonly and writable ownership;
6. function-valued callbacks/predicates that must not be invoked as
   getters;
7. errors and cleanup;
8. component unmount.

Do not create a composable merely to move unrelated code out of a
component.

## Compose Composables from Smaller Primitives

**BAD:**

```vue
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const x = ref(0)
const y = ref(0)
const inside = ref(false)
const el = ref(null)

function onMove(e) {
  x.value = e.pageX
  y.value = e.pageY
  if (!el.value) return
  const r = el.value.getBoundingClientRect()
  inside.value = x.value >= r.left && x.value <= r.right &&
    y.value >= r.top && y.value <= r.bottom
}

onMounted(() => window.addEventListener('mousemove', onMove))
onUnmounted(() => window.removeEventListener('mousemove', onMove))
</script>
```

**GOOD:**

```javascript
// composables/useEventListener.js
import { onMounted, onUnmounted, toValue } from 'vue'

export function useEventListener(target, event, callback) {
  onMounted(() => toValue(target).addEventListener(event, callback))
  onUnmounted(() => toValue(target).removeEventListener(event, callback))
}
```

```javascript
// composables/useMouse.js
import { ref } from 'vue'
import { useEventListener } from './useEventListener'

export function useMouse() {
  const x = ref(0)
  const y = ref(0)

  useEventListener(window, 'mousemove', (e) => {
    x.value = e.pageX
    y.value = e.pageY
  })

  return { x, y }
}
```

```javascript
// composables/useMouseInElement.js
import { computed } from 'vue'
import { useMouse } from './useMouse'

export function useMouseInElement(elementRef) {
  const { x, y } = useMouse()

  const isOutside = computed(() => {
    if (!elementRef.value) return true
    const rect = elementRef.value.getBoundingClientRect()
    return x.value < rect.left || x.value > rect.right ||
      y.value < rect.top || y.value > rect.bottom
  })

  return { x, y, isOutside }
}
```

## Use Options Object Pattern for Composable Parameters

**BAD:**

```javascript
export function useFetch(url, method, headers, timeout, retries, immediate) {
  // hard to read and easy to misorder
}

useFetch('/api/users', 'GET', null, 5000, 3, true)
```

**GOOD:**

```javascript
export function useFetch(url, options = {}) {
  const {
    method = 'GET',
    headers = {},
    timeout = 30000,
    retries = 0,
    immediate = true
  } = options

  // implementation
  return { method, headers, timeout, retries, immediate }
}

useFetch('/api/users', {
  method: 'POST',
  timeout: 5000,
  retries: 3
})
```

```typescript
interface UseCounterOptions {
  initial?: number
  min?: number
  max?: number
  step?: number
}

export function useCounter(options: UseCounterOptions = {}) {
  const { initial = 0, min = -Infinity, max = Infinity, step = 1 } = options
  // implementation
}
```

## Return Readonly State with Explicit Actions

**BAD:**

```javascript
export function useCart() {
  const items = ref([])
  const total = computed(() => items.value.reduce((sum, item) => sum + item.price, 0))
  return { items, total } // any consumer can mutate directly
}

const { items } = useCart()
items.value.push({ id: 1, price: 10 })
```

**GOOD:**

```javascript
import { ref, computed, readonly } from 'vue'

export function useCart() {
  const _items = ref([])

  const total = computed(() =>
    _items.value.reduce((sum, item) => sum + item.price * item.quantity, 0)
  )

  function addItem(product, quantity = 1) {
    const existing = _items.value.find(item => item.id === product.id)
    if (existing) {
      existing.quantity += quantity
      return
    }
    _items.value.push({ ...product, quantity })
  }

  function removeItem(productId) {
    _items.value = _items.value.filter(item => item.id !== productId)
  }

  return {
    items: readonly(_items),
    total,
    addItem,
    removeItem
  }
}
```

## Keep Utilities as Utilities

**BAD:**

```javascript
export function useFormatters() {
  const formatDate = (date) => new Intl.DateTimeFormat('en-US').format(date)
  const formatCurrency = (amount) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount)
  return { formatDate, formatCurrency }
}

const { formatDate } = useFormatters()
```

**GOOD:**

```javascript
// utils/formatters.js
export function formatDate(date) {
  return new Intl.DateTimeFormat('en-US').format(date)
}

export function formatCurrency(amount) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD'
  }).format(amount)
}
```

```javascript
// composables/useInvoiceSummary.js
import { computed } from 'vue'
import { formatCurrency } from '@/utils/formatters'

export function useInvoiceSummary(invoiceRef) {
  const totalLabel = computed(() => formatCurrency(invoiceRef.value.total))
  return { totalLabel }
}
```

## Organize Composable and Component Code by Feature Concern

**BAD:**

```vue
<script setup>
import { ref, computed, watch, onMounted } from 'vue'

const searchQuery = ref('')
const items = ref([])
const selected = ref(null)
const showModal = ref(false)
const sortBy = ref('name')
const filter = ref('all')
const loading = ref(false)

const filtered = computed(() => items.value.filter(i => i.category === filter.value))
function openModal() { showModal.value = true }
const sorted = computed(() => [...filtered.value].sort(/* ... */))
watch(searchQuery, () => { /* ... */ })
onMounted(() => { /* ... */ })
</script>
```

**GOOD:**

```vue
<script setup>
import { useItems } from '@/composables/useItems'
import { useSearch } from '@/composables/useSearch'
import { useSelectionModal } from '@/composables/useSelectionModal'

// Data
const { items, loading, fetchItems } = useItems()

// Search/filter/sort
const { query, visibleItems } = useSearch(items)

// Selection + modal
const { selectedItem, isModalOpen, selectItem, closeModal } = useSelectionModal()
</script>
```

```javascript
// composables/useItems.js
import { ref, onMounted } from 'vue'

export function useItems() {
  const items = ref([])
  const loading = ref(false)

  async function fetchItems() {
    loading.value = true
    try {
      items.value = await api.getItems()
    } finally {
      loading.value = false
    }
  }

  onMounted(fetchItems)
  return { items, loading, fetchItems }
}
```
