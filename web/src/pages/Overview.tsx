import { Card, EmptyState, PageHeader } from "../components/ui";

export default function Overview() {
  return (
    <>
      <PageHeader title="Overview" subtitle="Coming in the next step." />
      <Card><EmptyState title="Not built yet" /></Card>
    </>
  );
}
