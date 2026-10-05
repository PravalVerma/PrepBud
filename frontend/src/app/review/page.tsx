import type { Metadata } from "next";

import { ReviewQueue } from "@/components/review/review-queue";
import { StudyPlanView } from "@/components/review/study-plan";

export const metadata: Metadata = { title: "Review & plan" };

export default function ReviewPage() {
  return (
    <div className="space-y-6" data-testid="review-page">
      <ReviewQueue />
      <StudyPlanView />
    </div>
  );
}
