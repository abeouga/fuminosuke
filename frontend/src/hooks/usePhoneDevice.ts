import { useEffect, useState } from "react";

/**
 * True on a phone-sized touch device. Uses the device (screen and pointer), not the
 * viewport width, so browser zoom or enlarged text on a PC does not hide features.
 */
export function isPhoneDevice(): boolean {
  if (typeof window === "undefined") return false;
  const userAgent = window.navigator.userAgent;
  if (/iPhone|iPod|Android.+Mobile|Mobile.+Firefox|Windows Phone/i.test(userAgent)) return true;
  const coarse = window.matchMedia?.("(pointer: coarse)").matches ?? false;
  const shortSide = Math.min(window.screen?.width ?? Infinity, window.screen?.height ?? Infinity);
  return coarse && shortSide < 600;
}

export function usePhoneDevice(): boolean {
  const [phone, setPhone] = useState(isPhoneDevice);
  useEffect(() => {
    const update = () => setPhone(isPhoneDevice());
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
    };
  }, []);
  return phone;
}
