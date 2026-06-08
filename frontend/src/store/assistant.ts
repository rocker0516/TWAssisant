import { create } from "zustand";

export type ChatMsg = { role: "user" | "assistant"; content: string };

type AssistantState = {
  open: boolean;
  busy: boolean;
  messages: ChatMsg[];
  toggle: () => void;
  setOpen: (v: boolean) => void;
  setBusy: (v: boolean) => void;
  addUser: (content: string) => void;
  startAssistant: () => void;
  appendAssistant: (chunk: string) => void;
  reset: () => void;
};

// app 層級 store → 跨頁切換不消失（浮動助手對話保留）
export const useAssistant = create<AssistantState>((set) => ({
  open: false,
  busy: false,
  messages: [],
  toggle: () => set((s) => ({ open: !s.open })),
  setOpen: (v) => set({ open: v }),
  setBusy: (v) => set({ busy: v }),
  addUser: (content) => set((s) => ({ messages: [...s.messages, { role: "user", content }] })),
  startAssistant: () => set((s) => ({ messages: [...s.messages, { role: "assistant", content: "" }] })),
  appendAssistant: (chunk) =>
    set((s) => {
      const msgs = s.messages.slice();
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") msgs[msgs.length - 1] = { ...last, content: last.content + chunk };
      return { messages: msgs };
    }),
  reset: () => set({ messages: [] }),
}));
