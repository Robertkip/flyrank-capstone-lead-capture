/*!
 * Embeddable Widget & Lead-Capture Platform — widget loader.
 *
 * Loaded by one <script> tag on any website:
 *   <script src="https://api.example.com/embed/widget.v1.js?id=PUBLIC_ID" async></script>
 *
 * It reads its own src to learn which widget to render and which API to talk
 * to, fetches the public config, renders a form, and posts submissions back
 * cross-origin. No dependencies, no globals beyond one namespaced object.
 */
(function () {
  "use strict";

  var NS = "__leadCaptureWidget";
  if (window[NS] && window[NS].loaded) return;
  window[NS] = { loaded: true, instances: [] };

  // ---------------------------------------------------------------- discovery
  function currentScript() {
    if (document.currentScript) return document.currentScript;
    var scripts = document.getElementsByTagName("script");
    for (var i = scripts.length - 1; i >= 0; i--) {
      if (/\/embed\/widget\.[^/]+\.js/.test(scripts[i].src)) return scripts[i];
    }
    return null;
  }

  var script = currentScript();
  if (!script) return;

  var src;
  try {
    src = new URL(script.src, window.location.href);
  } catch (e) {
    return;
  }

  var widgetId = src.searchParams.get("id");
  if (!widgetId) {
    console.error("[lead-capture] missing ?id= on the embed script");
    return;
  }

  var apiBase = src.origin;
  var configUrl = apiBase + "/api/public/widgets/" + encodeURIComponent(widgetId) + "/config";

  // ------------------------------------------------------------------ styling
  var STYLE_ID = "lead-capture-widget-styles";
  function injectStyles(accent) {
    if (document.getElementById(STYLE_ID)) return;
    var css = [
      ".lcw{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:26rem;",
      "border:1px solid #e2e8f0;border-radius:.75rem;padding:1.25rem;background:#fff;color:#0f172a;",
      "box-shadow:0 1px 3px rgba(15,23,42,.08);box-sizing:border-box}",
      ".lcw *{box-sizing:border-box}",
      ".lcw h3{margin:0 0 .25rem;font-size:1.05rem;line-height:1.3}",
      ".lcw p.lcw-desc{margin:0 0 1rem;font-size:.85rem;color:#64748b;line-height:1.45}",
      ".lcw label{display:block;font-size:.78rem;font-weight:600;margin:.7rem 0 .25rem;color:#334155}",
      ".lcw input,.lcw textarea{width:100%;padding:.5rem .6rem;border:1px solid #cbd5e1;",
      "border-radius:.4rem;font-size:.88rem;font-family:inherit;background:#fff;color:#0f172a}",
      ".lcw input:focus,.lcw textarea:focus{outline:2px solid " + accent + ";outline-offset:1px;border-color:" + accent + "}",
      ".lcw textarea{min-height:4.5rem;resize:vertical}",
      ".lcw button{margin-top:1rem;width:100%;padding:.6rem;border:0;border-radius:.4rem;",
      "background:" + accent + ";color:#fff;font-size:.9rem;font-weight:600;cursor:pointer}",
      ".lcw button:disabled{opacity:.6;cursor:not-allowed}",
      ".lcw-hp{position:absolute!important;left:-9999px!important;width:1px!important;",
      "height:1px!important;opacity:0!important;pointer-events:none!important}",
      ".lcw-msg{margin-top:.85rem;font-size:.83rem;line-height:1.4}",
      ".lcw-msg.ok{color:#15803d}.lcw-msg.err{color:#b91c1c}",
      ".lcw-field-err{color:#b91c1c;font-size:.75rem;margin-top:.2rem}"
    ].join("");
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ------------------------------------------------------------------- helpers
  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      for (var key in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, key)) node.setAttribute(key, attrs[key]);
      }
    }
    if (text != null) node.textContent = text; // textContent, never innerHTML
    return node;
  }

  function mountPoint() {
    var explicit = document.querySelector('[data-lead-capture-widget="' + widgetId + '"]');
    if (explicit) return explicit;
    var generic = document.getElementById("lead-capture-widget");
    if (generic) return generic;
    var container = el("div");
    if (script.parentNode) script.parentNode.insertBefore(container, script.nextSibling);
    else document.body.appendChild(container);
    return container;
  }

  // -------------------------------------------------------------------- render
  function render(config, mount) {
    var accent = (config.display && config.display.accent) || "#2563eb";
    injectStyles(accent);

    var root = el("div", { class: "lcw", role: "form", "aria-label": config.title });
    root.appendChild(el("h3", null, config.title));
    if (config.description) root.appendChild(el("p", { class: "lcw-desc" }, config.description));

    var form = el("form", { novalidate: "novalidate" });
    var inputs = {};

    config.fields.forEach(function (field) {
      var inputId = "lcw-" + widgetId + "-" + field.name;
      form.appendChild(el("label", { for: inputId }, field.label + (field.required ? " *" : "")));

      var input;
      if (field.type === "textarea") {
        input = el("textarea", { id: inputId, name: field.name });
      } else {
        input = el("input", { id: inputId, name: field.name, type: field.type || "text" });
      }
      if (field.placeholder) input.setAttribute("placeholder", field.placeholder);
      if (field.required) input.setAttribute("aria-required", "true");

      inputs[field.name] = input;
      form.appendChild(input);
      form.appendChild(el("div", { class: "lcw-field-err", "data-for": field.name }));
    });

    // Honeypot: off-screen, aria-hidden, autocomplete off. Humans never fill it.
    var honeypot = el("input", {
      class: "lcw-hp",
      type: "text",
      name: config.honeypot_field,
      tabindex: "-1",
      autocomplete: "off",
      "aria-hidden": "true"
    });
    form.appendChild(honeypot);

    var button = el("button", { type: "submit" }, config.button_text);
    form.appendChild(button);

    var message = el("div", { class: "lcw-msg", role: "status", "aria-live": "polite" });
    form.appendChild(message);

    root.appendChild(form);
    mount.appendChild(root);

    var renderedAt = Date.now();

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      button.disabled = true;
      message.className = "lcw-msg";
      message.textContent = "";
      form.querySelectorAll(".lcw-field-err").forEach(function (n) { n.textContent = ""; });

      var data = {};
      Object.keys(inputs).forEach(function (name) {
        var value = inputs[name].value.trim();
        if (value !== "") data[name] = value;
      });

      var body = {
        widget_id: widgetId,
        data: data,
        honeypot: honeypot.value,
        elapsed_ms: Date.now() - renderedAt
      };

      fetch(config.submit_url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      })
        .then(function (response) {
          return response.json().then(function (payload) {
            return { status: response.status, payload: payload };
          });
        })
        .then(function (result) {
          if (result.status >= 200 && result.status < 300) {
            message.className = "lcw-msg ok";
            message.textContent = result.payload.message || config.success_message;
            form.reset();
            return;
          }

          message.className = "lcw-msg err";
          if (result.status === 429) {
            message.textContent = "Too many submissions right now — please try again shortly.";
          } else if (Array.isArray(result.payload.detail)) {
            message.textContent = "Please correct the highlighted fields.";
            result.payload.detail.forEach(function (item) {
              var target = form.querySelector('[data-for="' + item.field + '"]');
              if (target) target.textContent = item.message;
            });
          } else {
            message.textContent = result.payload.error || "Something went wrong.";
          }
        })
        .catch(function () {
          message.className = "lcw-msg err";
          message.textContent = "Network error — please try again.";
        })
        .then(function () {
          button.disabled = false;
        });
    });

    window[NS].instances.push({ id: widgetId, root: root });
  }

  // --------------------------------------------------------------------- boot
  function boot() {
    fetch(configUrl, { method: "GET" })
      .then(function (response) {
        if (!response.ok) throw new Error("config request failed: " + response.status);
        return response.json();
      })
      .then(function (config) {
        render(config, mountPoint());
      })
      .catch(function (error) {
        // A broken widget must never break the host page.
        console.error("[lead-capture] could not load widget " + widgetId, error);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
