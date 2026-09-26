// 极简 preload：UI 是纯 web（由 xy-gate 托管），无需暴露 Node 能力
const { contextBridge } = require("electron");
contextBridge.exposeInMainWorld("xyDesktop", { isDesktop: true });
