"use client";

import { RequireAuth } from "@/components/RequireAuth";
import { AdsLab } from "@/components/ads/AdsLab";

export default function AdsLabPage() {
  return (
    <RequireAuth>
      <AdsLab />
    </RequireAuth>
  );
}
