export interface SessionTimerTick {
  remainingSeconds: number;
  showOneMinuteWarning: boolean;
  expired: boolean;
}

export function tickSessionTimer(remainingSeconds: number, warningAlreadyShown: boolean): SessionTimerTick {
  const remaining = Math.max(0, remainingSeconds - 1);
  return {
    remainingSeconds: remaining,
    showOneMinuteWarning: remaining > 0 && remaining <= 60 && !warningAlreadyShown,
    expired: remaining === 0,
  };
}
