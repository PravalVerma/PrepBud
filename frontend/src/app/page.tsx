import { redirect } from "next/navigation";

import { HOME_PATH } from "@/lib/routes";

/** Landing: signed-in users go to the dashboard (the proxy sends others to /login). */
export default function Home() {
  redirect(HOME_PATH);
}
