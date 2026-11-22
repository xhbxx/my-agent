// Keep a visible fallback when a dependency fails before the app binds events.
import('./app.js').catch(error => {
  console.error('DATA-AGENT startup failed', error);
  document.getElementById('startup-status').hidden = true;
  document.getElementById('startup-recovery').hidden = false;
});
