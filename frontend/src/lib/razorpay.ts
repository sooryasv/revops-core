// Razorpay Standard Checkout (https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/).
// The script is injected only by the public pay page, never globally (03 section 10).
export const CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

export interface CheckoutSuccess {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}
export interface CheckoutOptions {
  key: string;
  amount: number;
  currency: string;
  order_id: string;
  name: string;
  description: string;
  handler: (response: CheckoutSuccess) => void;
  modal: { ondismiss: () => void };
  theme: { color: string };
}
interface CheckoutInstance {
  open: () => void;
  on: (event: "payment.failed", cb: (response: { error: { description?: string; reason?: string } }) => void) => void;
}
declare global {
  interface Window {
    Razorpay?: new (options: CheckoutOptions) => CheckoutInstance;
  }
}

let loading: Promise<void> | null = null;

export function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  loading ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = CHECKOUT_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      loading = null;
      script.remove();
      reject(new Error("Could not load the payment form."));
    };
    document.head.appendChild(script);
  });
  return loading;
}

export function openCheckout(options: CheckoutOptions, onFailed: (message: string) => void): void {
  if (!window.Razorpay) throw new Error("Payment form is not loaded.");
  const instance = new window.Razorpay(options);
  instance.on("payment.failed", (response) => onFailed(response.error.description ?? "The payment did not go through."));
  instance.open();
}
