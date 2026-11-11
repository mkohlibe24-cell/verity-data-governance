import { Card, EmptyState, PageHeader } from "../components/ui";

export default function Issues() {
  return (
    <>
      <PageHeader title="Issues" subtitle="Coming in the next step." />
      <Card><EmptyState title="Not built yet" /></Card>
    </>
  );
}
