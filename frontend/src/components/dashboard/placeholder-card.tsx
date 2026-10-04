import { Card, CardHeader } from "@/components/ui/card";
import { Badge, Skeleton } from "@/components/ui/feedback";

/** Skeleton panel for dashboard widgets delivered in later phases. */
export function PlaceholderCard({
  title,
  description,
  phase,
}: {
  title: string;
  description: string;
  phase: number;
}) {
  return (
    <Card>
      <CardHeader title={title} description={description} action={<Badge>Phase {phase}</Badge>} />
      <div className="space-y-2" aria-label={`${title} (coming soon)`}>
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
        <Skeleton className="h-3 w-2/3" />
      </div>
    </Card>
  );
}
