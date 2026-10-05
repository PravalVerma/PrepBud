import type { Metadata } from "next";

import { DocumentList } from "@/components/upload/document-list";
import { UploadPanel } from "@/components/upload/upload-panel";

export const metadata: Metadata = { title: "Upload material" };

export default function UploadPage() {
  return (
    <div className="space-y-6" data-testid="upload-page">
      <UploadPanel />
      <DocumentList />
    </div>
  );
}
