// Phone numbers for police calls: the form Twilio calls is "+91" and the
// 10-digit mobile number. Same rules as clean_phone() in
// backend/services/police_station_service.py.

export const PHONE_EXAMPLE = "+91 98765 43210";

// "+91 98765-43210", "9876543210", "09876543210", "919876543210" ->
// { phone: "+919876543210" }; anything else -> { error: "..." }.
export function cleanPhone(text) {
  let cleaned = (text || "").replace(/[^\d+]/g, "");
  let digits = cleaned.replace(/^\+/, "");
  if (!cleaned.startsWith("+")) {
    if (digits.length === 11 && digits.startsWith("0")) digits = digits.slice(1);
    if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
    if (digits.length === 10 && /^[6-9]/.test(digits)) cleaned = `+91${digits}`;
  }
  if (cleaned.startsWith("+91") && !/^\+91[6-9]\d{9}$/.test(cleaned)) {
    return { error: "After +91 a mobile number has 10 digits, starting with 6, 7, 8 or 9." };
  }
  if (!/^\+[1-9]\d{7,14}$/.test(cleaned)) {
    return { error: `Type +91 and the 10-digit mobile number, e.g. ${PHONE_EXAMPLE}.` };
  }
  return { phone: cleaned };
}

// "+919876543210" -> "+91 98765 43210" (easier to read back).
export function showPhone(phone) {
  const match = /^\+91(\d{5})(\d{5})$/.exec(phone || "");
  return match ? `+91 ${match[1]} ${match[2]}` : phone;
}
