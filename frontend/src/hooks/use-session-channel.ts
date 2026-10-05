"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";

import { sessionListKey } from "@/hooks/use-sessions";
import { api } from "@/lib/api";
import { SessionSocket } from "@/lib/session-socket";
import { useSessionStore } from "@/stores/session-store";
import type { ClientMessage, SessionQuestion } from "@/types/session";

/**
 * Opens the live channel for a session and exposes the student's actions. Events flow
 * into the session store; every action echoes locally first so the UI responds at once.
 */
export function useSessionChannel(sessionId: string, wsBaseUrl: string) {
  const socketRef = useRef<SessionSocket | null>(null);
  const questionShownAt = useRef(0);
  const queryClient = useQueryClient();
  const store = useSessionStore;

  useEffect(() => {
    const { reset, receive, setConnection } = store.getState();
    reset(sessionId);
    const socket = new SessionSocket({
      sessionId,
      baseUrl: wsBaseUrl,
      getTicket: api.getWsTicket,
      onEvent: (event) => {
        if (event.type === "question") questionShownAt.current = Date.now();
        receive(event);
        if (event.type === "session_ended") void queryClient.invalidateQueries({ queryKey: sessionListKey });
      },
      onStatus: setConnection,
    });
    socketRef.current = socket;
    void socket.connect();
    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [sessionId, wsBaseUrl, queryClient, store]);

  const send = useCallback((message: ClientMessage) => {
    const ok = socketRef.current?.send(message) ?? false;
    if (!ok) {
      store.getState().receive({
        type: "error",
        payload: {
          code: "NOT_CONNECTED",
          message: "You're offline — reconnecting. Try again in a moment.",
          retryable: true,
        },
      });
    }
    return ok;
  }, [store]);

  const answer = useCallback(
    (question: SessionQuestion, content: string) => {
      store.getState().answer(question.question_id, content);
      send({
        type: "student_response",
        payload: {
          content,
          question_id: question.question_id,
          time_taken_seconds: Math.max(0, Math.round((Date.now() - questionShownAt.current) / 1000)),
        },
      });
    },
    [send, store],
  );

  const ask = useCallback(
    (content: string) => {
      store.getState().studentText(content);
      send({ type: "student_question", payload: { content } });
    },
    [send, store],
  );

  const acknowledge = useCallback(
    (understood: boolean) => {
      store.getState().studentText(understood ? "Got it 👍" : "Can you explain it differently?");
      send({ type: "student_acknowledge", payload: { understood } });
    },
    [send, store],
  );

  const hint = useCallback(() => {
    store.getState().setBusy(true);
    send({ type: "request_hint", payload: {} });
  }, [send, store]);

  /** Ends over the socket, or over REST when the connection is down. */
  const end = useCallback(async () => {
    const state = store.getState();
    state.setBusy(true);
    if (socketRef.current?.isOpen && socketRef.current.send({ type: "end_session", payload: {} })) return;
    try {
      const view = await api.endSession(sessionId);
      for (const event of view.events ?? []) store.getState().receive(event);
      store.getState().view(view);
      void queryClient.invalidateQueries({ queryKey: sessionListKey });
    } catch (error) {
      store.getState().receive({
        type: "error",
        payload: { code: "END_FAILED", message: (error as Error).message, retryable: true },
      });
    }
  }, [queryClient, sessionId, store]);

  const reconnect = useCallback(() => socketRef.current?.retry(), []);

  return { answer, ask, acknowledge, hint, end, reconnect };
}
