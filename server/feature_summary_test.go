package main

import (
	"encoding/json"
	"testing"
)

func TestFeatureSummarySerialization(t *testing.T) {
	tests := []struct {
		name    string
		feature FeatureSummary
		checkFn func(*testing.T, FeatureSummary, []byte)
	}{
		{
			name: "all fields populated",
			feature: FeatureSummary{
				Title:       "Test Feature",
				Stage:       "spec-writing",
				Board:       "default",
				Created:     "2025-01-01",
				ID:          "f1",
				Description: "A test feature",
				Plan:        "This is the plan",
				ImplSpec:    "Implementation specification",
				TestSpec:    "Test specification",
				ImplNotes:   "Implementation notes",
			},
			checkFn: func(t *testing.T, original FeatureSummary, data []byte) {
				var decoded FeatureSummary
				if err := json.Unmarshal(data, &decoded); err != nil {
					t.Fatalf("unmarshal failed: %v", err)
				}
				if decoded.Plan != original.Plan {
					t.Errorf("plan = %q, want %q", decoded.Plan, original.Plan)
				}
				if decoded.ImplSpec != original.ImplSpec {
					t.Errorf("impl_spec = %q, want %q", decoded.ImplSpec, original.ImplSpec)
				}
				if decoded.TestSpec != original.TestSpec {
					t.Errorf("test_spec = %q, want %q", decoded.TestSpec, original.TestSpec)
				}
				if decoded.ImplNotes != original.ImplNotes {
					t.Errorf("impl_notes = %q, want %q", decoded.ImplNotes, original.ImplNotes)
				}
			},
		},
		{
			name: "empty fields omitted with omitempty",
			feature: FeatureSummary{
				Title: "Test Feature",
				Stage: "ideas",
				Board: "default",
				ID:    "f1",
			},
			checkFn: func(t *testing.T, original FeatureSummary, data []byte) {
				var decoded map[string]interface{}
				if err := json.Unmarshal(data, &decoded); err != nil {
					t.Fatalf("unmarshal failed: %v", err)
				}
				if _, has := decoded["plan"]; has {
					t.Error("plan should be omitted for empty string")
				}
				if _, has := decoded["impl_spec"]; has {
					t.Error("impl_spec should be omitted for empty string")
				}
				if _, has := decoded["test_spec"]; has {
					t.Error("test_spec should be omitted for empty string")
				}
				if _, has := decoded["impl_notes"]; has {
					t.Error("impl_notes should be omitted for empty string")
				}
			},
		},
		{
			name: "json tags correct",
			feature: FeatureSummary{
				Plan:      "my plan",
				ImplSpec:  "my impl spec",
				TestSpec:  "my test spec",
				ImplNotes: "my impl notes",
			},
			checkFn: func(t *testing.T, original FeatureSummary, data []byte) {
				var decoded map[string]interface{}
				if err := json.Unmarshal(data, &decoded); err != nil {
					t.Fatalf("unmarshal failed: %v", err)
				}
				if _, has := decoded["plan"]; !has {
					t.Error("json tag should be 'plan'")
				}
				if _, has := decoded["impl_spec"]; !has {
					t.Error("json tag should be 'impl_spec'")
				}
				if _, has := decoded["test_spec"]; !has {
					t.Error("json tag should be 'test_spec'")
				}
				if _, has := decoded["impl_notes"]; !has {
					t.Error("json tag should be 'impl_notes'")
				}
			},
		},
		{
			name: "special characters preserved",
			feature: FeatureSummary{
				Title:     "Test",
				Plan:      "Plan with <html> & \"quotes\"",
				ImplSpec:  "Impl with <script>alert('xss')</script>",
				TestSpec:  "Test with newlines\n\tand tabs",
				ImplNotes: "Notes with unicode: 日本語",
			},
			checkFn: func(t *testing.T, original FeatureSummary, data []byte) {
				var decoded FeatureSummary
				if err := json.Unmarshal(data, &decoded); err != nil {
					t.Fatalf("unmarshal failed: %v", err)
				}
				if decoded.Plan != original.Plan {
					t.Errorf("plan = %q, want %q", decoded.Plan, original.Plan)
				}
				if decoded.ImplSpec != original.ImplSpec {
					t.Errorf("impl_spec = %q, want %q", decoded.ImplSpec, original.ImplSpec)
				}
				if decoded.TestSpec != original.TestSpec {
					t.Errorf("test_spec = %q, want %q", decoded.TestSpec, original.TestSpec)
				}
				if decoded.ImplNotes != original.ImplNotes {
					t.Errorf("impl_notes = %q, want %q", decoded.ImplNotes, original.ImplNotes)
				}
			},
		},
		{
			name: "description also has omitempty",
			feature: FeatureSummary{
				Title:       "Test",
				Description: "",
			},
			checkFn: func(t *testing.T, original FeatureSummary, data []byte) {
				var decoded map[string]interface{}
				if err := json.Unmarshal(data, &decoded); err != nil {
					t.Fatalf("unmarshal failed: %v", err)
				}
				if _, has := decoded["description"]; has {
					t.Error("description should be omitted for empty string with omitempty")
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			data, err := json.Marshal(tt.feature)
			if err != nil {
				t.Fatalf("marshal failed: %v", err)
			}
			tt.checkFn(t, tt.feature, data)
		})
	}
}

func TestFeatureSummaryFieldPreservation(t *testing.T) {
	original := FeatureSummary{
		Title:       "Feature 1",
		Stage:       "plan-inbox",
		Board:       "default",
		Created:     "2025-01-01",
		ID:          "f1",
		Description: "Description here",
		Plan:        "Plan content",
		ImplSpec:    "Impl spec content",
		TestSpec:    "Test spec content",
		ImplNotes:   "Impl notes content",
	}

	data, err := json.Marshal(original)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded FeatureSummary
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.Title != original.Title {
		t.Errorf("title lost: got %q, want %q", decoded.Title, original.Title)
	}
	if decoded.Stage != original.Stage {
		t.Errorf("stage lost: got %q, want %q", decoded.Stage, original.Stage)
	}
	if decoded.Board != original.Board {
		t.Errorf("board lost: got %q, want %q", decoded.Board, original.Board)
	}
	if decoded.Description != original.Description {
		t.Errorf("description lost: got %q, want %q", decoded.Description, original.Description)
	}
	if decoded.Plan != original.Plan {
		t.Errorf("plan lost: got %q, want %q", decoded.Plan, original.Plan)
	}
	if decoded.ImplSpec != original.ImplSpec {
		t.Errorf("impl_spec lost: got %q, want %q", decoded.ImplSpec, original.ImplSpec)
	}
	if decoded.TestSpec != original.TestSpec {
		t.Errorf("test_spec lost: got %q, want %q", decoded.TestSpec, original.TestSpec)
	}
	if decoded.ImplNotes != original.ImplNotes {
		t.Errorf("impl_notes lost: got %q, want %q", decoded.ImplNotes, original.ImplNotes)
	}
}
