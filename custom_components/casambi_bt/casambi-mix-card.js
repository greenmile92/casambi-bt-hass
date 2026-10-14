// White/color mix of a Casambi RGB/TW or RGBW unit as one slider, like in the Casambi app.
// Left of the white dot: white 100 %, less color. Right of it: color 100 %, less white.
// Config: type: custom:casambi-mix-card, entity: number.<unit>_weiss_farbe, name (optional).

export const RAW_MAX = 63; // whitecolorbalance has 6 bits
export const RAW_MID = 31; // both full, measured against the app on 2026-10-13

/** HA state of the number (0–100) to the raw value, like raw_from_mix in number.py. */
export const rawFromState = (state) => Math.round((Number(state) * RAW_MAX) / 100);

/** Raw value to the number value to send, like mix_from_raw in number.py. */
export const stateFromRaw = (raw) => Math.round((raw * 100) / RAW_MAX);

/** Percentages the app shows for a raw value. */
export function percentFromRaw(raw) {
  if (raw <= RAW_MID) return { white: 100, color: Math.round((raw * 100) / RAW_MID) };
  return { white: Math.round(((RAW_MAX - raw) * 100) / (RAW_MAX - RAW_MID)), color: 100 };
}

export function labelFromRaw(raw) {
  const { white, color } = percentFromRaw(raw);
  return `${white}% / ${color}%`;
}

const STYLE = `
  ha-card {
    --casambi-accent: #d9913e;
    --casambi-line: var(--primary-text-color);
    --thumb: 26px;
    padding: 12px 16px 14px;
  }
  .name { color: var(--primary-text-color); font-size: 15px; margin-bottom: 8px; }
  .row { display: flex; align-items: center; gap: 16px; }
  .pill {
    flex: none;
    min-width: 6.5em;
    padding: 3px 10px;
    border: 1.5px solid var(--casambi-accent);
    border-radius: 999px;
    color: var(--primary-text-color);
    font-size: 15px;
    font-variant-numeric: tabular-nums;
    text-align: center;
  }
  .slider { position: relative; flex: 1; height: 40px; }
  .dot {
    position: absolute;
    top: 50%;
    left: calc(var(--thumb) / 2 + (100% - var(--thumb)) * ${RAW_MID} / ${RAW_MAX});
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--casambi-line);
    transform: translate(-50%, -50%);
    pointer-events: none;
  }
  input {
    position: relative;
    width: 100%;
    height: 100%;
    margin: 0;
    background: transparent;
    -webkit-appearance: none;
    appearance: none;
    cursor: pointer;
    touch-action: pan-y;
  }
  input:disabled { opacity: 0.4; cursor: default; }
  input:focus-visible { outline: 2px solid var(--casambi-accent); outline-offset: 4px; border-radius: 4px; }
  input::-webkit-slider-runnable-track { height: 3px; border-radius: 2px; background: var(--casambi-line); }
  input::-moz-range-track { height: 3px; border-radius: 2px; background: var(--casambi-line); }
  input::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: var(--thumb);
    height: var(--thumb);
    margin-top: calc(1.5px - var(--thumb) / 2);
    border: none;
    border-radius: 50%;
    background: var(--casambi-accent);
  }
  input::-moz-range-thumb {
    width: var(--thumb);
    height: var(--thumb);
    border: none;
    border-radius: 50%;
    background: var(--casambi-accent);
  }
`;

// ponytail: Node imports this file for the unit test, it has no DOM.
const Base = globalThis.HTMLElement ?? class {};

class CasambiMixCard extends Base {
  setConfig(config) {
    if (!config?.entity) throw new Error("entity fehlt (number.<gerät>_weiss_farbe)");
    this._config = config;
    this._state = undefined;
    if (!this.shadowRoot) this._build();
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 2;
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find((e) => e.endsWith("_weiss_farbe"));
    return { entity: entity ?? "" };
  }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${STYLE}</style>
      <ha-card>
        <div class="name"></div>
        <div class="row">
          <span class="pill"></span>
          <div class="slider">
            <span class="dot"></span>
            <input type="range" min="0" max="${RAW_MAX}" step="1">
          </div>
        </div>
      </ha-card>`;
    this._name = root.querySelector(".name");
    this._pill = root.querySelector(".pill");
    this._input = root.querySelector("input");
    // Live updates must not move the handle under the finger; send only on release.
    this._input.addEventListener("pointerdown", () => (this._dragging = true));
    this._input.addEventListener("pointerup", () => (this._dragging = false));
    this._input.addEventListener("pointercancel", () => (this._dragging = false));
    this._input.addEventListener("input", () => {
      this._pill.textContent = labelFromRaw(Number(this._input.value));
    });
    this._input.addEventListener("change", () => {
      this._dragging = false;
      this._send(Number(this._input.value));
    });
  }

  _update() {
    if (!this._hass || !this._config || !this._input) return;
    const st = this._hass.states[this._config.entity];
    const name = this._config.name ?? st?.attributes.friendly_name ?? this._config.entity;
    this._name.textContent = name;
    this._input.setAttribute("aria-label", name);
    // hass is replaced on every state change in HA, act only on our own entity.
    if (st === this._state || this._dragging) return;
    this._state = st;
    const value = parseFloat(st?.state);
    this._input.disabled = !Number.isFinite(value);
    if (this._input.disabled) {
      this._pill.textContent = "–";
      return;
    }
    const raw = rawFromState(value);
    this._input.value = String(raw);
    this._pill.textContent = labelFromRaw(raw);
  }

  _send(raw) {
    this._hass
      .callService("number", "set_value", {
        entity_id: this._config.entity,
        value: stateFromRaw(raw),
      })
      .catch((err) => {
        console.error("casambi-mix-card: set_value failed", err);
        this._state = undefined; // show the real state again
        this._update();
      });
  }
}

if (globalThis.customElements && !customElements.get("casambi-mix-card")) {
  customElements.define("casambi-mix-card", CasambiMixCard);
  (window.customCards ??= []).push({
    type: "casambi-mix-card",
    name: "Casambi Weiß / Farbe",
    description: "Mischregler wie in der Casambi-App",
  });
}
