const { contextBridge, ipcRenderer } = require("electron");

// The popup page can ONLY do these three things. Nothing else leaks through.
contextBridge.exposeInMainWorld("nudge", {
  onShow: (cb) => ipcRenderer.on("nudge:show", (_e, data) => cb(data)),
  feedback: (payload) => ipcRenderer.send("nudge:feedback", payload),
  close: () => ipcRenderer.send("nudge:close"),
});
