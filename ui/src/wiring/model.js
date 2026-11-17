// The Wiring page's model (docs/specs/wiring.md), on SAMPLE data: nothing here is read from or sent to the bench.
//
// This file is the contract the future `wiring.*` commands must satisfy: a device, the connections that belong to it,
// what each connection claims (an adapter, some pins), and the questions that build one. The UI holds no safety rule of
// its own in the finished product; the service will say what is available and refuse what is unsafe. Here the sample
// service is `store.js`.

// ---- device identity colour --------------------------------------------------------------------------------------
// One stable hue per device, from its position among the sorted serials, so two devices can never share one. Every class
// is a whole literal (Tailwind reads source text) with its dark partner.
export const HUES = [
  { name: "blue", dot: "bg-blue-500 dark:bg-blue-400", edge: "border-blue-500 dark:border-blue-400", tint: "bg-blue-50 dark:bg-blue-950", ink: "text-blue-800 dark:text-blue-300" },
  { name: "emerald", dot: "bg-emerald-500 dark:bg-emerald-400", edge: "border-emerald-500 dark:border-emerald-400", tint: "bg-emerald-50 dark:bg-emerald-950", ink: "text-emerald-800 dark:text-emerald-300" },
  { name: "purple", dot: "bg-purple-500 dark:bg-purple-400", edge: "border-purple-500 dark:border-purple-400", tint: "bg-purple-50 dark:bg-purple-950", ink: "text-purple-800 dark:text-purple-300" },
  { name: "amber", dot: "bg-amber-500 dark:bg-amber-400", edge: "border-amber-500 dark:border-amber-400", tint: "bg-amber-50 dark:bg-amber-950", ink: "text-amber-800 dark:text-amber-300" },
];

export function hueFor(devices, serial) {
  const order = devices.map((d) => d.serial).sort();
  return HUES[Math.max(0, order.indexOf(serial)) % HUES.length];
}

export const short = (id) => (id.length > 5 ? `${id.slice(0, 4)}…` : id);

// ---- the Pi's header (as in the GPIO page) ------------------------------------------------------------------------
// [physical pin, label, BCM number or null]
export const PI_HEADER = [
  [1, "3V3", null], [2, "5V", null], [3, "GPIO2", 2], [4, "5V", null], [5, "GPIO3", 3], [6, "GND", null],
  [7, "GPIO4", 4], [8, "GPIO14", 14], [9, "GND", null], [10, "GPIO15", 15], [11, "GPIO17", 17], [12, "GPIO18", 18],
  [13, "GPIO27", 27], [14, "GND", null], [15, "GPIO22", 22], [16, "GPIO23", 23], [17, "3V3", null], [18, "GPIO24", 24],
  [19, "GPIO10", 10], [20, "GND", null], [21, "GPIO9", 9], [22, "GPIO25", 25], [23, "GPIO11", 11], [24, "GPIO8", 8],
  [25, "GND", null], [26, "GPIO7", 7], [27, "ID_SDA", 0], [28, "ID_SCL", 1], [29, "GPIO5", 5], [30, "GND", null],
  [31, "GPIO6", 6], [32, "GPIO12", 12], [33, "GPIO13", 13], [34, "GND", null], [35, "GPIO19", 19], [36, "GPIO16", 16],
  [37, "GPIO26", 26], [38, "GPIO20", 20], [39, "GND", null], [40, "GPIO21", 21],
];
const RESERVED = new Set([0, 1]);
export const physicalPin = (gpio) => (PI_HEADER.find((r) => r[2] === gpio) || [null])[0];
export const freeGpios = () => PI_HEADER.filter((r) => r[2] !== null && !RESERVED.has(r[2])).map((r) => r[2]);

// A spare UART on Pi pins needs a device-tree overlay and a reboot (docs/specs/console.md): said on screen, not hidden.
export const PI_UARTS = [
  { id: "uart3", pins: [4, 5], label: "Pi pins 4 and 5 (UART3)" },
  { id: "uart4", pins: [8, 9], label: "Pi pins 8 and 9 (UART4)" },
  { id: "uart5", pins: [12, 13], label: "Pi pins 12 and 13 (UART5)" },
];
// A bus is SHARED: many chips may sit on one. The pins are marked as shared, never as owned.
export const BUSES = [
  { id: "i2c1", label: "I2C bus 1", pins: [2, 3], hint: "Pi pins 3 and 5. Many chips can share it." },
  { id: "spi0", label: "SPI bus 0", pins: [7, 8, 9, 10, 11], hint: "Pi pins 19, 21, 23, 24 and 26. Each chip has its own select line." },
  { id: "w1", label: "1-Wire", pins: [4], hint: "Pi pin 7. Many chips can share it." },
];
export const BAUDS = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600];   // never 1200: that is bootloader entry

// ---- sample data -------------------------------------------------------------------------------------------------
export function initialState() {
  return {
    devices: [
      { serial: "D953162EF5806B4B", model: "RAK4631", mode: "app",
        labels: { debug: "TX0 / RX0", reset: "RST", boot: "BOOT", power: "3V3", i2c: "SDA / SCL", swd: "SWD pins" } },
      { serial: "A1B2C3D4E5F60718", model: "RAK4631", mode: "bootloader",
        labels: { debug: "TX0 / RX0", reset: "RST", boot: "BOOT", power: "3V3", i2c: "SDA / SCL", swd: "SWD pins" } },
    ],
    adapters: [{ id: "5B1F007437", name: "USB Single Serial", ids: "1a86:55d4", node: "ttyACM1" }],
    probes: [{ id: "000760123456", name: "J-Link EDU Mini" }],
    connections: [
      { id: "c1", device: "D953162EF5806B4B", spec: { intent: "log", via: "adapter:5B1F007437", baud: 115200, send: "no" } },
      { id: "c2", device: "D953162EF5806B4B", spec: { intent: "control", fn: "reset", pin: 17, polarity: "high", voltage: "unmeasured", pulse: 100 } },
    ],
    dismissed: [],
    seq: 3,
  };
}
export const NEW_ADAPTER = { id: "8C2D1A9907", name: "FT232R USB UART", ids: "0403:6001", node: "ttyUSB0" };

// ---- what a connection claims ------------------------------------------------------------------------------------
// Keys: adapter:ID, pin:GPIO, probe:ID are EXCLUSIVE (one connection owns them); bus pins are shared.
export function claimsOf(spec) {
  const out = { exclusive: [], shared: [] };
  if (spec.intent === "log") {
    if (spec.via && spec.via.startsWith("adapter:")) out.exclusive.push(spec.via);
    if (spec.via && spec.via.startsWith("uart:")) (PI_UARTS.find((u) => `uart:${u.id}` === spec.via) || { pins: [] }).pins.forEach((p) => out.exclusive.push(`pin:${p}`));
  } else if (spec.intent === "control" && spec.pin != null) {
    out.exclusive.push(`pin:${spec.pin}`);
  } else if (spec.intent === "bus" && spec.bus) {
    (BUSES.find((b) => b.id === spec.bus) || { pins: [] }).pins.forEach((p) => out.shared.push(`pin:${p}`));
  } else if (spec.intent === "swd" && spec.probe) {
    out.exclusive.push(`probe:${spec.probe}`);
  }
  return out;
}

// {key: connection} for every exclusive claim, ignoring one connection (the one being edited).
export function holders(connections, ignoreId) {
  const map = {};
  connections.forEach((c) => {
    if (c.id === ignoreId) return;
    claimsOf(c.spec).exclusive.forEach((k) => { map[k] = c; });
  });
  return map;
}
export function sharedPins(connections) {
  const map = {};
  connections.forEach((c) => claimsOf(c.spec).shared.forEach((k) => { (map[k] = map[k] || []).push(c); }));
  return map;
}

// ---- the questions -----------------------------------------------------------------------------------------------
export const INTENTS = [
  { value: "log", label: "Read its debug log", hint: "Watch what the firmware prints." },
  { value: "control", label: "Reset, boot or power it", hint: "A Pi pin that drives one of its control pins." },
  { value: "bus", label: "Talk to a sensor or chip", hint: "I2C, SPI or 1-Wire, with the Pi in charge." },
  { value: "swd", label: "Program or debug it over SWD", hint: "A debug probe on its SWD pins." },
  { value: "analog", label: "Measure a voltage", hint: "The Pi cannot do this alone; it needs an ADC chip." },
];
const INTENT_LABEL = Object.fromEntries(INTENTS.map((i) => [i.value, i.label]));

const VOLTAGE_OPTIONS = [
  { value: "measured", label: "I measured 3.3 V", hint: "With a meter, on the wire, with both ends powered." },
  { value: "unmeasured", label: "I haven't measured it", hint: "That is fine. It stays recorded only, and nothing is sent or driven until it is measured." },
];

export const exclusiveTaken = (ctx, key) => ctx.holders[key] || null;
const holderText = (c, ctx) => {
  const dev = ctx.devices.find((d) => d.serial === c.device);
  const what = c.spec.intent === "log" ? "debug log" : c.spec.intent === "control" ? `${c.spec.fn} line` : c.spec.intent === "swd" ? "SWD connection" : "connection";
  return `${what} of ${dev ? `${dev.model} ${short(dev.serial)}` : "another device"}`;
};

// Each question: id, title, type (choice | chips | text | number), visible(spec), options(ctx, spec), answer(spec, ctx).
const QUESTIONS = {
  log: [
    {
      id: "via", title: "Through what?", type: "choice",
      options: (ctx) => [
        ...ctx.adapters.map((a) => {
          const taken = exclusiveTaken(ctx, `adapter:${a.id}`);
          return { value: `adapter:${a.id}`, label: `USB adapter ${a.name}`, hint: `${a.ids}, serial ${a.id}, ${a.node}`,
                   disabled: Boolean(taken), why: taken ? `Already used by the ${holderText(taken, ctx)}.` : "" };
        }),
        ...PI_UARTS.map((u) => {
          const taken = u.pins.map((p) => exclusiveTaken(ctx, `pin:${p}`)).find(Boolean);
          return { value: `uart:${u.id}`, label: u.label, hint: "Needs a Pi setting change (a device-tree overlay) and a reboot.",
                   disabled: Boolean(taken), why: taken ? `Pin already used by the ${holderText(taken, ctx)}.` : "" };
        }),
      ],
      answer: (spec, ctx) => {
        const a = ctx.adapters.find((x) => `adapter:${x.id}` === spec.via);
        if (a) return `USB adapter ${a.name} (${short(a.id)})`;
        const u = PI_UARTS.find((x) => `uart:${x.id}` === spec.via);
        return u ? u.label : "";
      },
    },
    {
      id: "baud", title: "What baud rate does its firmware use?", type: "chips", hint: "It is set in the firmware. There is no suggested value.",
      options: () => BAUDS.map((b) => ({ value: b, label: String(b) })), answer: (spec) => `${spec.baud} baud`,
    },
    {
      id: "send", title: "Should the bench be able to send to it?", type: "choice",
      options: () => [
        { value: "no", label: "No, only watch", hint: "Safe: only the device's TX needs to be wired to the adapter." },
        { value: "yes", label: "Yes, let the bench send commands", hint: "The adapter's TX must also reach the device's RX." },
      ],
      answer: (spec) => (spec.send === "yes" ? "The bench may send (once the voltage is measured)" : "Receive only"),
    },
    {
      id: "voltage", title: "What voltage is the adapter's signal?", type: "choice",
      hint: "A 5 V adapter on a 3.3 V pin damages the board.",
      visible: (spec) => spec.send === "yes",
      options: () => VOLTAGE_OPTIONS, answer: (spec) => (spec.voltage === "measured" ? "3.3 V, measured" : "Not measured yet"),
    },
  ],
  control: [
    {
      id: "fn", title: "What should the pin do?", type: "choice",
      options: () => [
        { value: "reset", label: "Reset it", hint: "A short pulse on its reset pin." },
        { value: "boot", label: "Hold it in its bootloader", hint: "Holds its boot (DFU) pin while it resets." },
        { value: "power", label: "Switch its power", hint: "Drives a switch or relay on its supply." },
      ],
      answer: (spec) => ({ reset: "Reset it", boot: "Hold it in its bootloader", power: "Switch its power" })[spec.fn],
    },
    {
      id: "pin", title: "Which Pi pin?", type: "chips",
      options: (ctx) => freeGpios().map((g) => {
        const taken = exclusiveTaken(ctx, `pin:${g}`);
        return { value: g, label: `GPIO${g} · pin ${physicalPin(g)}`, disabled: Boolean(taken), why: taken ? `GPIO${g}: ${holderText(taken, ctx)}.` : "" };
      }),
      answer: (spec) => `GPIO${spec.pin} (pin ${physicalPin(spec.pin)})`,
    },
    {
      id: "polarity", title: "Which level makes it act?", type: "choice",
      options: () => [
        { value: "high", label: "High (the pin goes to 3.3 V)", hint: "Active high." },
        { value: "low", label: "Low (the pin goes to ground)", hint: "Active low. Many reset pins work this way." },
      ],
      answer: (spec) => (spec.polarity === "high" ? "Acts when high" : "Acts when low"),
    },
    {
      id: "voltage", title: "What voltage is on that wire?", type: "choice", hint: "A header pin is 3.3 V and is not 5 V tolerant.",
      options: () => VOLTAGE_OPTIONS, answer: (spec) => (spec.voltage === "measured" ? "3.3 V, measured" : "Not measured yet"),
    },
    {
      id: "pulse", title: "Longest pulse it may ever be given (ms)", type: "number",
      hint: "Work it out from what the line drives. There is no suggested value.",
      answer: (spec) => `${spec.pulse} ms`,
    },
  ],
  bus: [
    {
      id: "bus", title: "Which bus?", type: "choice",
      options: (ctx) => BUSES.map((b) => {
        const n = (ctx.shared[`pin:${b.pins[0]}`] || []).length;
        return { value: b.id, label: b.label, hint: n ? `${b.hint} Already used by ${n} connection${n === 1 ? "" : "s"}.` : b.hint };
      }),
      answer: (spec) => (BUSES.find((b) => b.id === spec.bus) || {}).label,
    },
    {
      id: "target", title: "What is it?", type: "text", hint: "A name you will recognise, such as BME280 or ADS1115.",
      answer: (spec) => spec.target,
    },
  ],
  swd: [
    {
      id: "probe", title: "Through which probe?", type: "choice",
      options: (ctx) => ctx.probes.map((p) => {
        const taken = exclusiveTaken(ctx, `probe:${p.id}`);
        return { value: p.id, label: p.name, hint: `Serial ${p.id}`, disabled: Boolean(taken), why: taken ? `Already used by the ${holderText(taken, ctx)}.` : "" };
      }),
      empty: "No SWD probe is plugged in. Plug a J-Link or CMSIS-DAP probe into the Pi and it will appear here.",
      answer: (spec, ctx) => (ctx.probes.find((p) => p.id === spec.probe) || {}).name,
    },
  ],
  analog: [
    {
      id: "adc", title: "Which ADC chip?", type: "choice",
      options: (ctx) => ctx.connections.filter((c) => c.spec.intent === "bus" && /adc|ads|mcp3/i.test(c.spec.target || "")).map((c) => ({
        value: c.id, label: c.spec.target, hint: `on ${(BUSES.find((b) => b.id === c.spec.bus) || {}).label}`,
      })),
      empty: "The Pi has no analog input of its own. Connect an ADC chip (such as an ADS1115) on a bus first, then pick it here.",
      emptyAction: { label: "Connect the ADC chip first", intent: "bus" },
      answer: (spec, ctx) => (ctx.connections.find((c) => c.id === spec.adc) || { spec: {} }).spec.target,
    },
    { id: "channel", title: "Which input on the ADC chip?", type: "text", hint: "For example A0.", answer: (spec) => spec.channel },
  ],
};

export function questionsFor(intent) {
  return QUESTIONS[intent] || [];
}
const answered = (spec, q) => spec[q.id] !== undefined && spec[q.id] !== null && spec[q.id] !== "";
export const visibleQuestions = (spec) => questionsFor(spec.intent).filter((q) => !q.visible || q.visible(spec));
export const isComplete = (spec) => Boolean(spec.intent) && visibleQuestions(spec).every((q) => answered(spec, q));
export const firstOpen = (spec) => (spec.intent ? visibleQuestions(spec).find((q) => !answered(spec, q)) || null : null);
export const isAnswered = answered;

// Change one answer. Later answers are KEPT (changing the baud does not unsay whether the bench may send); only an
// answer to a question that no longer applies is dropped (turning sending off drops the voltage question).
export function withAnswer(spec, qid, value) {
  const next = { ...spec, [qid]: value };
  questionsFor(spec.intent).forEach((q) => { if (q.visible && !q.visible(next)) delete next[q.id]; });
  return next;
}

// ---- how a connection is described -----------------------------------------------------------------------------
// The wire sentence: where it comes from on the Pi, what it carries, where it lands on the device, and what it says.
export function describe(conn, ctx) {
  const spec = conn.spec;
  const dev = ctx.devices.find((d) => d.serial === conn.device);
  const devName = dev ? `${dev.model} ${short(dev.serial)}` : "the device";
  const L = (dev && dev.labels) || {};
  const out = { pi: { title: "", detail: "" }, signal: "", device: { title: devName, detail: "" }, summary: "", chips: [] };
  if (spec.intent === "log") {
    const a = ctx.adapters.find((x) => `adapter:${x.id}` === spec.via);
    const u = PI_UARTS.find((x) => `uart:${x.id}` === spec.via);
    out.pi = a ? { title: "USB adapter", detail: `${a.name} · ${short(a.id)}` } : { title: u ? u.label.replace(/ \(.*\)/, "") : "Pi pins", detail: u ? u.label.replace(/.*\(|\)/g, "") : "" };
    out.signal = L.debug || "debug UART";
    out.device.detail = "debug UART";
    const canSend = spec.send === "yes" && spec.voltage === "measured";
    out.summary = `Reading the debug log of ${devName} through ${a ? `USB adapter ${a.name}` : u ? u.label : "?"} at ${spec.baud} baud, ${canSend ? "and the bench may send to it" : spec.send === "yes" ? "receive only until the voltage is measured" : "receive only"}.`;
    out.chips.push({ tone: "green", text: "listening" }, canSend ? { tone: "blue", text: "can send" } : { tone: "amber", text: "receive only" });
  } else if (spec.intent === "control") {
    out.pi = { title: `Pi pin ${physicalPin(spec.pin)}`, detail: `GPIO${spec.pin}` };
    out.signal = ({ reset: L.reset || "reset", boot: L.boot || "boot", power: L.power || "power" })[spec.fn];
    out.device.detail = `${spec.fn} pin`;
    const armed = spec.voltage === "measured";
    out.summary = `${({ reset: "Resets", boot: "Holds in its bootloader", power: "Switches the power of" })[spec.fn]} ${devName} with GPIO${spec.pin}: it acts when ${spec.polarity}, in pulses of at most ${spec.pulse} ms${armed ? "." : ", and does nothing until the voltage is measured."}`;
    out.chips.push(armed ? { tone: "green", text: "ready" } : { tone: "amber", text: "recorded only" });
  } else if (spec.intent === "bus") {
    const b = BUSES.find((x) => x.id === spec.bus) || { label: "a bus", pins: [] };
    out.pi = { title: b.label, detail: `Pi pins ${b.pins.map(physicalPin).join(", ")}` };
    out.signal = L.i2c || "bus";
    out.device.title = spec.target;
    out.device.detail = `with ${devName}`;
    out.summary = `${devName} talks to ${spec.target} over ${b.label}, with the Pi in charge.`;
    out.chips.push({ tone: "slate", text: "shared bus" });
  } else if (spec.intent === "swd") {
    const p = ctx.probes.find((x) => x.id === spec.probe) || { name: "SWD probe" };
    out.pi = { title: "SWD probe", detail: p.name };
    out.signal = L.swd || "SWD";
    out.device.detail = "SWD pins";
    out.summary = `Programming and debugging ${devName} over SWD through ${p.name}.`;
    out.chips.push({ tone: "slate", text: "attached" });
  } else if (spec.intent === "analog") {
    const c = ctx.connections.find((x) => x.id === spec.adc);
    out.pi = { title: "ADC chip", detail: c ? c.spec.target : "?" };
    out.signal = `input ${spec.channel}`;
    out.device.detail = "analog out";
    out.summary = `Measuring a voltage from ${devName} with ${c ? c.spec.target : "the ADC chip"}, input ${spec.channel}.`;
    out.chips.push({ tone: "slate", text: "via ADC" });
  }
  return out;
}

export const intentLabel = (intent) => INTENT_LABEL[intent] || intent;
export function whatPinDoes(conn) {
  const s = conn.spec;
  if (s.intent === "control") return s.fn;
  if (s.intent === "log") return "debug log";
  if (s.intent === "bus") return s.target;
  return s.intent;
}
