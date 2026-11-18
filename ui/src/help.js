// The Help topics (docs/specs/dashboard-redesign.md, "Help"). DATA, not markup, so a test can check it: every page has a topic, every
// `related` link resolves, and every refusal code named here exists in the service. Each topic is short enough for one phone screen.
// Shape: {id, title, pages, summary, legend: [{state, term, text}], points: [text], steps: [text], problems: [{symptom, cause, fix}],
// codes: [{code, fix}], related: [id]}.

export const TOPICS = [
  {
    id: "start",
    title: "Start here",
    pages: ["home"],
    summary: "The bench is a Raspberry Pi that lets you, a script or an AI see and drive nRF and ESP32 boards.",
    legend: [
      { state: "connected", term: "Connected, listening", text: "It is there and working." },
      { state: "absent", term: "Not attached", text: "The bench knows about it, but it is not plugged in." },
      { state: "attention", term: "Needs attention", text: "It is there but something is wrong. The line under it says what." },
      { state: "fault", term: "Fault", text: "A command that went wrong." },
    ],
    points: [
      "The sentence at the top is the state of the bench. If it says everything is connected and nothing needs attention, you are done.",
      "Every heading and every row is a link. Tap Devices, a board, a wire or the Pi to open its page.",
      "The Pi header shows each pin as a dot: blue is I2C, green is a serial link, violet is anything else in the pin map, an empty ring is free.",
      "Devices lists every board and adapter. Wiring connects a board to something. Console reads its log. Firmware flashes it.",
    ],
    related: ["devices", "wiring", "trouble"],
  },
  {
    id: "devices",
    title: "Devices",
    pages: ["devices"],
    summary: "Every board and adapter the bench knows, plugged in or not.",
    legend: [
      { state: "connected", term: "Connected", text: "Plugged in and fine. This is the first tab." },
      { state: "absent", term: "Not attached", text: "A board you named or wired that is not plugged in now. See the second tab." },
      { state: "attention", term: "Needs attention", text: "Plugged in but not usable yet, such as an ESP32 nobody has declared." },
    ],
    steps: [
      "Open a row to see its serial, port and what it is wired to.",
      "Name an nRF board with Name it. The name is kept against its USB serial, so it follows the board to any port.",
      "Flash an nRF board from its row. An ESP32 board or adapter hands over to the page that owns it.",
    ],
    problems: [
      { symptom: "A board is not listed", cause: "It is not plugged in, or the cable carries power only.", fix: "Try another cable and port. A plug or unplug appears here by itself." },
      { symptom: "An ESP32 says it needs attention", cause: "A USB id cannot say what is behind an adapter, so an ESP32 is only one once you declare it.", fix: "Open it and choose Declare it, then pick a name." },
      { symptom: "A board is in both modes at once", cause: "Its application and its bootloader both show.", fix: "Unplug it and plug it in again." },
    ],
    related: ["flashing", "esp32", "trouble"],
  },
  {
    id: "wiring",
    title: "Wiring a debug log",
    pages: ["wiring"],
    summary: "Say which board a serial adapter is wired to, and its log appears on the Console.",
    steps: [
      "Plug in a USB serial adapter, or use the Pi's own serial port (header pins 8 and 10). It appears under Wiring as waiting to be connected.",
      "Choose the board it is wired to, then Set up the connection.",
      "Pick the baud rate the board's firmware uses. 115200 is the usual one.",
      "Choose Receive only to watch, or let the bench send. Sending needs a wired TX line, and for a USB adapter someone must have measured its voltage.",
      "Connect. The wire turns to listening and its lines show on the Console.",
    ],
    problems: [
      { symptom: "The adapter is not offered", cause: "It is not plugged in, has no serial number, or another connection already uses it.", fix: "Plug it in. The list updates by itself." },
      { symptom: "It says listening but nothing prints", cause: "The firmware prints in bursts or the baud rate is wrong.", fix: "Check the baud rate. Some firmware prints only every minute or so." },
      { symptom: "Sending is refused", cause: "The link is receive only, or a USB adapter's voltage is not recorded as measured.", fix: "Edit the connection: turn sending on, and say the voltage has been measured." },
    ],
    codes: [
      { code: "adapter_taken", fix: "Disconnect the other connection that uses this adapter first." },
      { code: "port_held", fix: "Another program has the port open. Close it." },
      { code: "intent_not_built", fix: "Only the debug log can be wired so far." },
    ],
    related: ["console", "pins", "trouble"],
  },
  {
    id: "console",
    title: "The console",
    pages: ["console"],
    summary: "A board's serial log, live, with a line to type into.",
    steps: [
      "Choose the link at the top. The log scrolls while you are at the end of it and stays put when you scroll up.",
      "Type a line and press Enter to send it. The up and down arrows bring back what was sent before.",
      "Save a line you send often as a macro, and run it from the Macros panel.",
      "On an ESP32 link, open Decode a crash, choose the bundle that was flashed and press Decode to turn a Backtrace into function names.",
    ],
    problems: [
      { symptom: "The input is off", cause: "The link is receive only, or its voltage is not recorded as measured.", fix: "The reason shows beside it. Change the connection on Wiring." },
      { symptom: "A macro name will not save", cause: "A name is lower case letters, digits, dot, underscore or hyphen.", fix: "Capitals are made lower case as you type. Anything else is explained under the box." },
      { symptom: "The log is empty after a restart", cause: "The history is kept in the service's memory.", fix: "It fills again as the board prints." },
    ],
    codes: [
      { code: "link_rx_only", fix: "Turn sending on in the connection." },
      { code: "voltage_unverified", fix: "Measure the adapter's voltage and record it, then turn sending on." },
    ],
    related: ["wiring", "esp32", "trouble"],
  },
  {
    id: "flashing",
    title: "Flashing",
    pages: ["firmware"],
    summary: "Put a firmware package into a project folder, then flash it to a board. Nothing is flashed until you press the button.",
    steps: [
      "Make a project folder, or choose one, on the Firmware page.",
      "Upload the package. For an nRF board that is a DFU .zip. For an ESP32 it is a whole-flash .bin, or a zip with flasher_args.json.",
      "Press Flash on the package and confirm. An nRF board goes into its bootloader by itself. A flash takes about 20 seconds.",
      "Check the result: a good flash says the board is back running its application. Read the whole answer.",
    ],
    problems: [
      { symptom: "The package is refused", cause: "It is the wrong kind, or an ESP32 .bin that is only the application.", fix: "Upload a DFU .zip for nRF, or a merged image or bundle for ESP32." },
      { symptom: "The flash says suspect", cause: "It finished but the evidence does not add up.", fix: "Treat the board as unknown. Look at its console, then flash again." },
    ],
    codes: [
      { code: "wrong_softdevice", fix: "The package is for a different SoftDevice. Build it for this board." },
      { code: "bad_package", fix: "The file is not a package the bench can flash. Its message says why." },
      { code: "protected", fix: "The board is marked protected (mid-measurement). Remove the mark if you mean to flash it." },
      { code: "lock_held", fix: "Another flash or reset is running. Wait for it to finish." },
      { code: "no_bound", fix: "Give a time limit. The dialog works one out for you." },
      { code: "flash_failed", fix: "Read the whole output in the result. Nothing is retried for you." },
    ],
    related: ["esp32", "devices", "trouble"],
  },
  {
    id: "esp32",
    title: "ESP32 boards",
    pages: [],
    summary: "An ESP32 is declared once, then flashed, reset and read like any other board.",
    steps: [
      "Plug the board in. Under ESP32 boards on the Firmware page, choose its adapter and give it a name, then Declare board.",
      "Identify asks the chip what it is. Reset runs its application. Hold in bootloader leaves it waiting to be flashed.",
      "Rename changes only the name. Forget removes it and its console.",
      "Its console reads the board's log. Opening the console does not reset a board on the adapters measured so far.",
    ],
    problems: [
      { symptom: "Identify fails", cause: "The adapter is not wired to the chip's reset, or the chip is asleep.", fix: "Try Hold in bootloader, or reset it by hand and try again." },
      { symptom: "The first line after a reset is missing", cause: "The console reopens just after the reset.", fix: "Expected. The later lines are all there." },
    ],
    codes: [
      { code: "target_unknown", fix: "No ESP32 has that name. Declare it first." },
      { code: "serial_unverifiable", fix: "Two adapters report the same serial. Declare it by its USB port instead." },
    ],
    related: ["flashing", "console", "trouble"],
  },
  {
    id: "pins",
    title: "Pins, I2C and the UART",
    pages: ["gpio"],
    summary: "Which Pi header pin is used for what, and how to turn on I2C and the serial port.",
    legend: [
      { state: "connected", term: "Header GPIO is 3.3 V", text: "The Pi's pins are 3.3 V by design, so no voltage needs measuring." },
    ],
    steps: [
      "Assign a pin on the GPIO page: its name, what it is for and which service uses it. Nothing is driven or read by assigning.",
      "I2C: assign BCM 2 and BCM 3 (header pins 3 and 5) with service i2c. Then Commands can scan and transfer on bus 1.",
      "The serial port on header pins 8 and 10 is a debug link once enabled. Its setup steps are listed by wiring uart_status on the Commands page.",
      "Turning on I2C or the serial port changes the Pi's boot settings and needs a reboot. The bench never does that by itself.",
    ],
    problems: [
      { symptom: "A pin cannot be assigned", cause: "A wiring connection holds it, another service has it, or it is reserved.", fix: "The message names who holds it. Disconnect that first." },
      { symptom: "An I2C scan finds nothing", cause: "A device that sleeps misses its first probe.", fix: "Probe its address again." },
    ],
    codes: [
      { code: "pin_taken", fix: "Another assignment or a connection holds this pin." },
      { code: "pin_reserved", fix: "The HAT's own pins are never offered." },
      { code: "i2c_pins_unclaimed", fix: "Assign BCM 2 and 3 with service i2c first." },
      { code: "voltage_unsafe", fix: "A header pin is 3.3 V at most." },
    ],
    related: ["wiring", "commands", "trouble"],
  },
  {
    id: "commands",
    title: "Commands, the API and AI tools",
    pages: ["commands", "activity"],
    summary: "Everything the dashboard does is a command, and the same commands are open to scripts and AI.",
    steps: [
      "Commands lists every command on this host. Search, choose one, fill the form and run it. A box with a list offers what the bench knows, such as the attached boards.",
      "A command that changes things says so. The whole answer is shown, with the evidence.",
      "Activity lists every command anyone has run, newest first, and how it ended. A refusal is as visible as a success.",
      "For a script or an AI, open /api/guide on this address. It explains every call and every result code. /api/openapi.json is the same for tools.",
      "There is a command line (nrfwb) and an MCP server with one tool per command, built from the same list.",
    ],
    problems: [
      { symptom: "A command is missing", cause: "This host does not have what it needs, such as a Pi or a folder.", fix: "Home lists the commands that are not available and what each needs." },
    ],
    related: ["start", "trouble"],
  },
  {
    id: "trouble",
    title: "If something is wrong",
    pages: [],
    summary: "Find the symptom, then the fix.",
    problems: [
      { symptom: "The page looks out of date", cause: "Live updates dropped.", fix: "Use Reconnect at the top. Everything refreshes after a command anyway." },
      { symptom: "A board is not listed", cause: "Not plugged in, or a power-only cable.", fix: "Try another cable. See Devices." },
      { symptom: "A flash or reset is refused", cause: "Another one is running, or the board is protected.", fix: "Wait, or check the result's code below." },
      { symptom: "Nothing prints on the console", cause: "Wrong baud rate, wrong wires, or the firmware is quiet.", fix: "Check the baud rate on Wiring and the TX and RX wires." },
      { symptom: "Sending is refused", cause: "Receive only, or voltage not recorded.", fix: "See The console." },
      { symptom: "A port is held", cause: "Another program has it open.", fix: "Close it. On the Pi's own serial port, the serial login must be off." },
    ],
    codes: [
      { code: "node_absent", fix: "The device is not attached, or has no port yet. Plug it in or give it a moment." },
      { code: "serial_mismatch", fix: "The attached board's serial is not the one named." },
      { code: "name_taken", fix: "Choose a different name." },
      { code: "capability_missing", fix: "A tool this command needs is not installed on the bench." },
      { code: "no_reply", fix: "The device did not answer. A sleeping device needs a second try." },
    ],
    related: ["start", "commands"],
  },
];

// The topic a page opens when its ? is pressed.
export function topicForPage(page) {
  const t = TOPICS.find((x) => x.pages.includes(page));
  return t ? t.id : "start";
}

// A short plain-text form of a topic, for the search box.
export function topicText(t) {
  const parts = [t.title, t.summary, ...(t.points || []), ...(t.steps || [])];
  (t.legend || []).forEach((l) => parts.push(l.term, l.text));
  (t.problems || []).forEach((p) => parts.push(p.symptom, p.cause, p.fix));
  (t.codes || []).forEach((c) => parts.push(c.code, c.fix));
  return parts.join(" ").toLowerCase();
}
