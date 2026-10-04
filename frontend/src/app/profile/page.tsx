"use client";

import { ProfileForm } from "@/components/profile/profile-form";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Skeleton } from "@/components/ui/feedback";
import { useProfile, useUpdateProfile } from "@/hooks/use-profile";

export default function ProfilePage() {
  const { data: profile, isLoading, error } = useProfile();
  const update = useUpdateProfile();

  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader
          title="Learning profile"
          description="Your tutor uses this to pitch explanations at the right level."
        />
        {isLoading ? (
          <div className="space-y-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : error || !profile ? (
          <Alert tone="error">Could not load your profile. {error?.message}</Alert>
        ) : (
          <ProfileForm
            profile={profile}
            onSubmit={(changes) => update.mutateAsync(changes)}
            saving={update.isPending}
          />
        )}
      </Card>
    </div>
  );
}
