// 统一 API 客户端：全部走相对路径（生产=同源 8790，开发=vite proxy）
export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const text = await res.text();
  let data: any = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text.slice(0, 200) }; }
  if (!res.ok && data && !data.error) data.error = `HTTP ${res.status}`;
  return data as T;
}

export const getStatus = () => api("/status");
export const getEvents = (limit = 50) => api(`/events?limit=${limit}`);
export const getHistory = (chatId: string, limit = 30) => api(`/history?chat_id=${encodeURIComponent(chatId)}&limit=${limit}`);
export const getItems = (seller = false) => api(`/items${seller ? "?source=seller" : ""}`);
export const getCapability = () => api("/capability");
export const getFloor = (itemId: string) => api(`/floor/${itemId}`);
export const postSearch = (keyword: string, min?: number, max?: number) =>
  api("/search", { method: "POST", body: JSON.stringify({ keyword, price_min: min, price_max: max }) });
export const postReprice = (itemId: string, newPrice: number, why = "后台改价") =>
  api("/reprice", { method: "POST", body: JSON.stringify({ item_id: itemId, new_price: newPrice, source: why }) });
export const postOffline = (ids: string[]) => api("/offline", { method: "POST", body: JSON.stringify({ item_ids: ids }) });
export const getOrders = (q: "NOT_SHIP" | "ALL" = "NOT_SHIP") => api(`/orders?query=${q}`);
export const getConfirms = () => api("/confirm/history");
export const postConfirmCheck = (b: object) => api("/confirm/check", { method: "POST", body: JSON.stringify(b) });
export const postConfirm = (b: object) => api("/confirm", { method: "POST", body: JSON.stringify(b) });
export const postSend = (chatId: string, toUserId: string, text: string) =>
  api("/send", { method: "POST", body: JSON.stringify({ chat_id: chatId, to_user_id: toUserId, text, why: "后台手动" }) });
export const getSnipeHits = () => api("/snipe/hits");
export const postSnipeRun = () => api("/snipe/run", { method: "POST", body: "{}" });
export const postSnipeHandle = (itemId: string) => api("/snipe/handle", { method: "POST", body: JSON.stringify({ item_id: itemId }) });
export const postAudit = () => api("/audit/run", { method: "POST", body: "{}" });
export const getSettings = () => api("/api/settings");
export const putSettings = (s: object) => api("/api/settings", { method: "PUT", body: JSON.stringify(s) });
export const getAccount = () => api("/api/account");
export const putAccount = (cookies: string) => api("/api/account", { method: "PUT", body: JSON.stringify({ cookies }) });
export const getDshKey = () => api("/api/dsh-key");
export const putDshKey = (k: string, baseUrl = "", model = "") => api("/api/dsh-key", { method: "PUT", body: JSON.stringify({ api_key: k, base_url: baseUrl, model }) });
export const postChat = (message: string) => api("/api/chat", { method: "POST", body: JSON.stringify({ message }) });
export const getChatHistory = () => api("/api/chat/history");
export const captureStart = () => api("/api/cookie-capture/start", { method: "POST", body: "{}" });
export const captureStatus = () => api("/api/cookie-capture/status");
export const captureStop = () => api("/api/cookie-capture/stop", { method: "POST", body: "{}" });
export const getConversations = () => api("/api/conversations");
export const markConversationRead = (chat: string) =>
  api(`/api/conversations/${encodeURIComponent(chat)}/read`, { method: "POST" });

export const postHotelCost = (hotel: string, price?: number) =>
  api("/api/hotel/cost", { method: "POST", body: JSON.stringify({ hotel, price }) });
