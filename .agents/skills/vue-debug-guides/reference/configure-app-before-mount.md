---
title: Configure Vue App Before Calling mount()
impact: HIGH
impactDescription: Late app configuration can miss initial render, injection and plugin startup; configure before mount.
type: capability
tags: [vue3, createApp, mount, configuration, setup]
---

# Configure Vue App Before Calling mount ()

**Impact: HIGH** — Configure plugins, handlers and registrations before the
initial mount so they participate in startup and the first render. It is not
correct to claim that every post-mount assignment is silently ignored:
`app.config.errorHandler`, for example, can affect later errors. Late plugin
installation still cannot retroactively satisfy earlier injections or guards.
Keep mount as the final initialization step and verify the actual failed
phase instead of generalizing all late configuration to one symptom.

## Task Checklist

- [ ] Register all plugins (router, store, etc.) before mount ()
- [ ] Configure error handlers before mount ()
- [ ] Register global components and directives before mount ()
- [ ] Set all `app.config` properties before mount ()
- [ ] Call `.mount()` as the final step in app initialization

**Incorrect:**

```javascript
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'

const app = createApp(App)

// WRONG: Mounting first, then configuring
app.mount('#app')

// Too late for startup/initial-render behavior; later effects are API-specific.
app.use(router)
app.config.errorHandler = (err) => {
  console.error('Global error:', err)
}
app.component('GlobalButton', GlobalButton) // Assumes the component is imported
```

**Correct:**

```javascript
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { createPinia } from 'pinia'
import GlobalButton from './components/GlobalButton.vue'

const app = createApp(App)

// Configure everything FIRST
app.use(createPinia()) // Install before router guards that use stores
app.use(router)

// Set up error handling
app.config.errorHandler = (err, instance, info) => {
  console.error('Global error:', err)
  console.log('Component:', instance)
  console.log('Error info:', info)
}

// Register global components
app.component('GlobalButton', GlobalButton)

// Mount LAST - after all configuration is complete
app.mount('#app')
```

## Common Mistake: Chaining with Mount

```javascript
// WRONG: Chaining mount in the middle of configuration
createApp(App)
  .use(router)
  .mount('#app')  // Everything after this line is a problem
  .use(pinia)     // This doesn't even work - mount returns component instance!

// CORRECT: Either complete chain before mount, or use intermediate variable
createApp(App)
  .use(pinia)
  .use(router)
  .component('GlobalButton', GlobalButton)
  .mount('#app')  // Mount at the very end
```

## Reference

- [Vue.js - Creating a Vue Application](https://vuejs.org/guide/essentials/application.html)
- [Vue.js Application API](https://vuejs.org/api/application.html)
