import {
  flexRender, getCoreRowModel, getFilteredRowModel, getPaginationRowModel, getSortedRowModel, useReactTable,
  type ColumnDef, type SortingState,
} from "@tanstack/react-table";
import clsx from "clsx";
import { useState } from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from "lucide-react";
import { Button, EmptyState } from "./ui";

/** Client-side table: sorting, global filter and pagination. */
export function DataTable<T>({ data, columns, filter, pageSize = 15, onRowClick, empty = "No rows", dense }: {
  data: T[]; columns: ColumnDef<T, any>[]; filter?: string; pageSize?: number; onRowClick?: (row: T) => void;
  empty?: string; dense?: boolean;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data, columns,
    state: { sorting, globalFilter: filter ?? "" },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize } },
    globalFilterFn: "includesString",
  });
  const rows = table.getRowModel().rows;
  const total = table.getFilteredRowModel().rows.length;
  const { pageIndex } = table.getState().pagination;
  return (
    <div>
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-surface-2">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const sorted = h.column.getIsSorted();
                  return (
                    <th
                      key={h.id}
                      scope="col"
                      className="border-b border-border px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted"
                      aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                    >
                      {h.column.getCanSort() ? (
                        <button className="inline-flex items-center gap-1 hover:text-text" onClick={h.column.getToggleSortingHandler()}>
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {sorted === "asc" && <ArrowUp className="h-3 w-3" aria-hidden />}
                          {sorted === "desc" && <ArrowDown className="h-3 w-3" aria-hidden />}
                        </button>
                      ) : flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                onClick={onRowClick ? () => onRowClick(r.original) : undefined}
                onKeyDown={onRowClick ? (e) => e.key === "Enter" && onRowClick(r.original) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                className={clsx("border-b border-border last:border-0", onRowClick && "cursor-pointer hover:bg-surface-2")}
              >
                {r.getVisibleCells().map((c) => (
                  <td key={c.id} className={clsx("px-3 align-top text-text", dense ? "py-1.5" : "py-2")}>
                    {flexRender(c.column.columnDef.cell, c.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <EmptyState title={empty} />}
      </div>
      {total > pageSize && (
        <div className="mt-3 flex items-center justify-between text-xs text-muted">
          <span className="num">
            {pageIndex * pageSize + 1}–{Math.min((pageIndex + 1) * pageSize, total)} of {total}
          </span>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()} aria-label="Previous page">
              <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button size="sm" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()} aria-label="Next page">
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
