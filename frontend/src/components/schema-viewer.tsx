"use client";

import React, { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

export type SemanticType =
  | "identifier"
  | "metric"
  | "dimension"
  | "date"
  | "currency"
  | "percentage"
  | "boolean"
  | "text_description"
  | "geographic"
  | "unknown";

export interface ColumnMeta {
  name: string;
  original_name: string;
  dtype: string;
  semantic_type: SemanticType;
  business_label: string;
  null_pct: number;
  unique_pct: number;
  sample_values: any[];
  is_primary_key: boolean;
  is_candidate_kpi: boolean;
}

interface SchemaViewerProps {
  initialColumns: ColumnMeta[];
  onConfirm: (columns: ColumnMeta[]) => void;
}

const semanticTypeColors: Record<SemanticType, string> = {
  metric: "bg-green-500/20 text-green-700 hover:bg-green-500/30",
  currency: "bg-green-500/20 text-green-700 hover:bg-green-500/30",
  percentage: "bg-green-500/20 text-green-700 hover:bg-green-500/30",
  dimension: "bg-blue-500/20 text-blue-700 hover:bg-blue-500/30",
  identifier: "bg-orange-500/20 text-orange-700 hover:bg-orange-500/30",
  date: "bg-purple-500/20 text-purple-700 hover:bg-purple-500/30",
  boolean: "bg-slate-500/20 text-slate-700 hover:bg-slate-500/30",
  text_description: "bg-slate-500/20 text-slate-700 hover:bg-slate-500/30",
  geographic: "bg-blue-500/20 text-blue-700 hover:bg-blue-500/30",
  unknown: "bg-gray-500/20 text-gray-700 hover:bg-gray-500/30",
};

export function SchemaViewer({ initialColumns, onConfirm }: SchemaViewerProps) {
  const [columns, setColumns] = useState<ColumnMeta[]>(initialColumns);
  const [selectedColumnName, setSelectedColumnName] = useState<string | null>(
    initialColumns.length > 0 ? initialColumns[0].name : null
  );
  const [hasChanges, setHasChanges] = useState(false);

  const updateColumn = (name: string, updates: Partial<ColumnMeta>) => {
    setColumns((prev) =>
      prev.map((col) => (col.name === name ? { ...col, ...updates } : col))
    );
    setHasChanges(true);
  };

  const selectedColumn = columns.find((c) => c.name === selectedColumnName);

  return (
    <div className="flex h-[600px] w-full flex-col gap-4 overflow-hidden rounded-lg border bg-background md:flex-row">
      <div className="flex flex-1 flex-col overflow-hidden">
        <ScrollArea className="flex-1">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Column Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Semantic Type</TableHead>
                <TableHead>Business Label</TableHead>
                <TableHead>Null %</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {columns.map((col) => (
                <TableRow
                  key={col.name}
                  className={`cursor-pointer transition-colors ${
                    selectedColumnName === col.name ? "bg-muted/50" : ""
                  }`}
                  onClick={() => setSelectedColumnName(col.name)}
                >
                  <TableCell className="font-mono text-sm">{col.name}</TableCell>
                  <TableCell>
                    <Badge
                      variant="secondary"
                      className={semanticTypeColors[col.semantic_type]}
                    >
                      {col.dtype}
                    </Badge>
                  </TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Select
                      value={col.semantic_type}
                      onValueChange={(val) =>
                        updateColumn(col.name, {
                          semantic_type: val as SemanticType,
                        })
                      }
                    >
                      <SelectTrigger className="w-[140px] h-8 text-xs">
                        <SelectValue placeholder="Select type" />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.keys(semanticTypeColors).map((type) => (
                          <SelectItem key={type} value={type}>
                            {type}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Input
                      value={col.business_label}
                      onChange={(e) =>
                        updateColumn(col.name, {
                          business_label: e.target.value,
                        })
                      }
                      className="h-8 text-sm"
                      placeholder="e.g. Monthly Revenue"
                    />
                  </TableCell>
                  <TableCell className="w-[120px]">
                    <div className="flex items-center gap-2">
                      <Progress value={col.null_pct * 100} className="h-2 flex-1" />
                      <span className="text-xs text-muted-foreground w-8">
                        {Math.round(col.null_pct * 100)}%
                      </span>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </ScrollArea>
        <div className="border-t p-4 flex justify-end bg-muted/20">
          <Button
            disabled={!hasChanges}
            onClick={() => {
              onConfirm(columns);
              setHasChanges(false);
            }}
          >
            Confirm Schema & Continue
          </Button>
        </div>
      </div>

      {selectedColumn && (
        <div className="w-full border-l bg-muted/10 p-4 md:w-72 flex flex-col">
          <h3 className="font-semibold mb-4 text-sm uppercase text-muted-foreground">
            Column Details
          </h3>
          <div className="space-y-4 text-sm">
            <div>
              <span className="font-medium">Name: </span>
              <span className="font-mono text-muted-foreground">
                {selectedColumn.name}
              </span>
            </div>
            <div>
              <span className="font-medium">Original: </span>
              <span className="font-mono text-muted-foreground">
                {selectedColumn.original_name}
              </span>
            </div>
            <div>
              <span className="font-medium">Uniqueness: </span>
              <span className="text-muted-foreground">
                {Math.round(selectedColumn.unique_pct * 100)}%
              </span>
            </div>
            {selectedColumn.is_primary_key && (
              <Badge variant="outline" className="text-blue-500 border-blue-500">
                Primary Key Candidate
              </Badge>
            )}
            <div className="pt-4">
              <h4 className="font-medium mb-2">Sample Values</h4>
              <div className="flex flex-col gap-2">
                {selectedColumn.sample_values.length > 0 ? (
                  selectedColumn.sample_values.map((val, idx) => (
                    <div
                      key={idx}
                      className="bg-background rounded border px-2 py-1 font-mono text-xs truncate"
                      title={String(val)}
                    >
                      {String(val)}
                    </div>
                  ))
                ) : (
                  <span className="text-muted-foreground italic text-xs">
                    No samples available
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
