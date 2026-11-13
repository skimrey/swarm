---
id: policy-applet-html
title: The applet iframe protocol -- what the platform actually requires
subject: policy
subcategory: html
tags: [applet, html, survey, iframe, postmessage, submit, button, form, design, artifact, manifest, ready, autosave, draft, timer, validation, prepopulate, star, rating, widget, custom, slider, radio, queryselector, delegation]
priority: 8
summary: The Submit button's only hard requirement is window.parent.postMessage({type:'SUBMIT', output:{...}, timeSpentMs}, '*') on click, but guard it against a double-click anyway. Fire APPLET_READY on load (outbound only -- never listen for it) and start the timer from the UNIT_DATA reply. The inbound UNIT_DATA message carries `unit` and `prepopulate`, not `unit_data` -- restore `prepopulate` into form state instead of always wiping to blank. Send STATE_UPDATE on every change for autosave. Reset state on a new UNIT_DATA only when there is no prepopulate to restore. Post ERROR instead of failing silently. Never rely on native HTML5 validation for a hidden input synced from a custom widget -- show your own visible error instead. A custom widget (star rating, slider, toggle group) built from non-form elements never fires the native input/change events the boilerplate's delegated listener catches -- build it from real radio inputs, or call the state-update function directly from its own click handler. A widget rendered once per item (one rating row per food) needs querySelectorAll+forEach or delegation, never a singular querySelector on the shared class -- that silently binds only the first instance. Never emit a _platform_* key.
updated: 2026-09-23
---

An applet's HTML runs inside the platform's iframe, and it talks to the page
that embeds it entirely through `window.postMessage` -- there is no other
channel. Getting this wrong is invisible to every gate here: the file is
valid HTML, opens fine standalone, and only breaks once it is actually
running inside the platform's iframe, which nothing in this pipeline can
    10|simulate.

**The one hard requirement.** The Submit button's click handler must fire
this and nothing more is required to satisfy the platform:

```javascript
window.parent.postMessage({
  type: 'SUBMIT',
  output: { /* the annotation JSON this applet collects */ },
  timeSpentMs: Date.now() - startTime  // informational -- the platform
    20|                                       // tracks real payable time itself
}, '*');
```

No disabled state, confirmation dialog, or validation is *enforced* by the
platform. Anything beyond the message above -- a "are you sure?" dialog, a
disabled-until-valid button -- is a design choice this applet is free to
make, not something to invent because it sounds safer. Do not gate `SUBMIT`
behind a check the platform never asked for; that is a way to accidentally
block a user who filled the form out correctly.

    30|**Guard the click handler against itself, even though the platform does
not ask for it.** Nothing about the message above stops the same handler
firing twice from one double-click, and the platform has no dedup on its
side -- it just receives two `SUBMIT` messages. Set a local flag (or
disable the button) the instant the handler runs, before the
`postMessage` call, and return immediately if it is already set:

```javascript
let submitted = false;
submitButton.addEventListener('click', () => {
  if (submitted) return;
  submitted = true;
  submitButton.disabled = true;
    40|  submitButton.textContent = 'Submitting...';
  window.parent.postMessage({ type: 'SUBMIT', output, timeSpentMs }, '*');
});
```

This is not the same claim as the disabled-state paragraph above: that one
says the platform will accept a submission with no visual affordance at
all, and this one says a missing affordance lets one user action produce
two messages. Both are true. Add the guard for correctness, not because
the platform demands it.

    50|**The inbound message: `UNIT_DATA` carries `unit` and `prepopulate`, not
`unit_data`.** The parent replies to `APPLET_READY` with exactly:

```javascript
window.addEventListener('message', (event) => {
  const { type, unit, prepopulate } = event.data;
  if (type === 'UNIT_DATA') {
    startTime = Date.now();          // timer starts here, not on page load
    if (prepopulate && Object.keys(prepopulate).length > 0) {
      restoreFromDraft(prepopulate); // fill the form from the saved draft
    } else {
    60|      resetToBlank();               // no draft -- start clean
    }
  }
});
```

Two mistakes have both happened for the same underlying reason -- guessing
the shape of a message instead of reading it: destructuring a field called
`unit_data` (it is `unit`), and never reading `prepopulate` at all. The
platform supports restoring an in-progress answer after a refresh or a
closed tab specifically by attaching it to this field; an applet that
    70|always resets to blank on `UNIT_DATA` silently discards that feature even
though the platform did its half of the work. Reset state unconditionally
only when `prepopulate` is empty or absent -- reset *unconditionally on
every message* is still wrong, because it wipes a draft the platform just
handed back.

**`APPLET_READY` is outbound only. Never listen for it.** The applet sends
`APPLET_READY`; the parent never sends it back, and there is no message
flow in which this applet's own `window.addEventListener('message', ...)`
handler would ever see `type === 'APPLET_READY'`. A branch inside that
    80|handler checking for it is dead code reacting to a message that cannot
arrive -- delete it rather than leaving it for the next reader to wonder
about.

**The rest of the protocol, all one-directional except the reply noted:**

- Fire `postMessage({type: 'APPLET_READY'}, '*')` once, on load, before the
  `UNIT_DATA` handler above can do anything.
- Send `postMessage({type: 'STATE_UPDATE', ...}, '*')` whenever the
  in-progress answer changes. The platform autosaves from this at roughly
    90|  30-second intervals for draft recovery -- this is what later arrives
  back as `prepopulate`, so an applet that never sends `STATE_UPDATE` has
  nothing to restore.
- If `UNIT_DATA` never arrives, or arrives malformed (missing fields the
  form needs), post `{type: 'ERROR', message: '...'}` rather than failing
  silently, throwing uncaught, or leaving the iframe stuck showing an
  empty or broken form with no signal to the parent.
- Do not build a QA/rating widget inside the applet. That is platform
  chrome rendered outside this iframe, and duplicating it inside is
  redundant at best and conflicting at worst.
   100|- Never emit a key beginning `_platform_` in `output` -- that namespace is
  reserved by the platform, and a collision there is not caught by any
  schema this applet can see.
- Responsive layout (media queries) is recommended, not enforced. Prefer it
  when the survey has more than a couple of fields.

**Do not rely on native HTML5 validation for a hidden input synced from a
custom widget.** A pattern like a color wheel writing its choice into a
`<input type="hidden" required>` looks validated -- the browser will in
fact block the form -- but most browsers cannot anchor the validation
   110|bubble to an element with no visible box, so the block is silent: the
user clicks Submit, nothing visibly happens, and there is no text
explaining why. This is worse than no validation, because a user reading
a plain HTML form assumes silence means it worked. For any required field
that is not itself a visible, focusable input the user directly interacts
with, check its value in JavaScript before submitting and show your own
visible error message (a text node, not `alert()`) next to the widget
that fills it, rather than adding `required` to the hidden field and
trusting the browser to explain itself.

**A custom widget does not save for free -- it has to be wired to save.**
The reference boilerplate's event wiring is delegation: one `input`/
`change` listener on the whole form, catching whatever bubbles up. That
works automatically for a real `<input>`, `<textarea>`, `<select>`, or a
real `<input type="radio">`/`<input type="checkbox">`, because those fire
`input`/`change` natively. It does nothing for a widget built from plain
`<span>`/`<div>` elements with your own `click` handler -- clicking one
does not fire `input` or `change` on anything, so the delegated listener
never runs, and the state-saving function it would have called never
runs either. Measured live: a star-rating widget built this way updates
its own CSS (`classList.add('rated')`) perfectly on click -- the stars
visibly fill in -- and nothing is ever saved, because nothing in the click
handler tells the rest of the page a value changed.

Two ways to make a non-native widget actually save. Best: build it out of
real form elements -- a star rating is a row of
`<input type="radio" name="food" value="1">`...`value="5"` with `<label>`
elements styled to look like stars (hide the input with CSS, not the
label); clicking a label fires a real `change` event the existing
delegation already catches, and reading the value back is the same one
line as any other field. If the widget genuinely cannot be a form
element, call the state-update function directly from its own click
handler -- never assume bubbling will find it -- and make sure whatever
builds the final output actually reads the widget's current value,
not just its CSS state. A click handler that only ever calls
`classList.add`/`classList.remove` has changed what the screen shows and
nothing else.

**A widget rendered once per item needs its listener wired once per
item, not once for the whole page.** A survey that asks the same
question for several items (rate each of five foods, one row per option)
renders the same widget class several times -- `.stars` exists once per
food, not once total. `document.querySelector('.stars')` returns only the
*first* matching element in the whole document, silently: no error, no
warning, and the click handler you attach to it only ever fires for that
first item. Every later instance renders pixel-identical to the working
one and is permanently inert. This is invisible in testing a single-item
form and only appears once there is more than one, which is exactly the
shape a rating-per-item survey always has. Use
`document.querySelectorAll(theSameClass).forEach(el => el.addEventListener(...))`,
or attach one listener to a parent that already exists before any item is
rendered and let the event bubble to it -- never a bare, singular
`querySelector` on a class this same page renders more than once.
