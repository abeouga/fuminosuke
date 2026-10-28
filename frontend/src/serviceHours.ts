import { useEffect, useState } from "react";

// The cloud stops the server at night. The schedule is a static file next to the screen (S3 via CloudFront),
// so it can be read while the server itself is down. Without the file (local development) nothing is shown.
export type ServiceHours = { enabled: boolean; openHour: number; closeHour: number };

// After the opening hour the server takes a few minutes to start (the schedule checks every few minutes,
// and the database starts after that), so a failed connection soon after opening is reported as starting.
const STARTING_MINUTES = 30;

let loaded: Promise<ServiceHours | null> | null = null;

function load(): Promise<ServiceHours | null> {
  loaded ??= fetch("/service-hours.json", { cache: "no-store" })
    .then((response) => response.ok ? response.json() : null)
    .then((value: unknown) => {
      const hours = value as Partial<ServiceHours> | null;
      if (!hours || typeof hours.enabled !== "boolean") return null;
      const valid = (hour: unknown) => typeof hour === "number" && Number.isInteger(hour) && hour >= 0 && hour <= 23;
      if (!valid(hours.openHour) || !valid(hours.closeHour) || hours.openHour === hours.closeHour) return null;
      return hours as ServiceHours;
    })
    .catch(() => null);
  return loaded;
}

// Minutes since midnight in Japan, where the schedule is set.
function japanMinutes(now: Date): number {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(now);
  const part = (type: string) => Number(parts.find((item) => item.type === type)?.value ?? 0);
  return part("hour") * 60 + part("minute");
}

export type ServiceHoursNotice = { title: string; detail: string };

export function serviceHoursNotice(hours: ServiceHours | null, reachable: boolean, now = new Date()): ServiceHoursNotice | null {
  if (!hours?.enabled) return null;
  const minutes = japanMinutes(now);
  const open = hours.openHour * 60;
  const close = hours.closeHour * 60;
  const closed = close > open ? minutes >= close || minutes < open : minutes >= close && minutes < open;
  const range = `${hours.closeHour}:00〜${hours.openHour}:00`;
  if (closed) return { title: `${range}は使えません`, detail: `${hours.openHour}:00から使えます。` };
  const sinceOpen = (minutes - open + 24 * 60) % (24 * 60);
  if (!reachable && sinceOpen < STARTING_MINUTES) {
    return { title: "サーバーを起動しています", detail: `${hours.openHour}:00に起動を始めました。数分で使えるようになります。` };
  }
  return null;
}

// Re-evaluated every 30 seconds so the notice appears at the closing time and goes away at the opening time.
export function useServiceHoursNotice(reachable: boolean): ServiceHoursNotice | null {
  const [hours, setHours] = useState<ServiceHours | null>(null);
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let stopped = false;
    void load().then((value) => { if (!stopped) setHours(value); });
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, []);
  return serviceHoursNotice(hours, reachable, now);
}
