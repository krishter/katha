"use client";

import { QrCode } from "@/components/QrCode";

/**
 * The family-number confirm link as a QR code, for a laptop. The message
 * has to be sent from the child's own WhatsApp number, which in practice
 * means their phone.
 */
export function ConfirmNumberQr({ link }: { link: string }) {
  return (
    <div className="mt-3 flex items-center gap-4">
      <QrCode
        value={link}
        size={136}
        label="QR code that opens WhatsApp to confirm your number"
      />
      <p className="text-sm text-ink-mid">
        Scan this with your phone&apos;s camera. WhatsApp opens with a message
        filled in; send it. It has to come from your own number, so use your
        phone.
      </p>
    </div>
  );
}
