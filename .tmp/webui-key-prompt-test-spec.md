# WebUI Key Authentication Test Specification

## Overview
This test specification validates the implementation of a modal overlay that prompts for a dashboard key, stored in localStorage. Without a valid key, the backend must not send any actual data to the frontend.

---

## 1. Behaviors That MUST Be Verified

### Phase 1: Modal Rendering Behavior

**Behavior 1.1: Modal Visibility for Unauthenticated Users**
- When no dashboard key is configured, users should see the normal dashboard without any modal
- When a dashboard key IS configured but user has not entered it, the key modal should be visible immediately upon page load
- The modal should NOT be hidden behind authentication checks - it must render for unauthenticated users

**Success Criteria:**
- The modal HTML is present in the DOM regardless of authentication state
- Unauthenticated users see the modal overlay prompting for the key
- The modal appears BEFORE any authentication check completes

**Expected Output:**
- Page renders with modal visible
- No blank screen or empty page displayed to unauthenticated users

**Behavior 1.2: Modal Positioning in Template**
- The key modal container must be placed OUTSIDE any authentication conditional blocks in the HTML template
- The modal should have its own conditional rendering logic independent of the main authenticated content

**Success Criteria:**
- Modal HTML appears before the `{{if .Authenticated}}` block in index.html
- Backend passes a separate `ShowKeyModal` or equivalent boolean to control modal visibility

### Phase 2: Server-Side Authentication Enforcement

**Behavior 2.1: No Data Sent to Unauthenticated Clients**
- When user is NOT authenticated, the backend must NOT fetch or send any client data
- The rendered page should contain empty arrays for Clients, Features, and StageGroups
- API endpoints should return 401 status for unauthenticated requests

**Success Criteria:**
- Root handler checks authentication BEFORE fetching data
- Unauthenticated responses contain empty data structures
- API calls without valid key receive 401 Unauthorized response

**Expected Output:**
- HTTP 401 status for unauthenticated API requests
- Empty JSON arrays in API responses when unauthenticated

**Behavior 2.2: Key Validation Logic**
- The system should accept either an empty key (when no dashboard key is configured) or the correct dashboard key
- Key validation should compare the provided key against the configured dashboard key
- Query parameter "key" should be used for authentication

**Success Criteria:**
- No key provided + no dashboard key configured = authenticated
- Correct key provided = authenticated
- Wrong key provided = unauthenticated

**Behavior 2.3: Conditional Modal Display**
- The modal should only appear when a dashboard key IS actually configured
- When no key is configured, the page should render normally without any modal

**Success Criteria:**
- `ShowKeyModal` is true when dashboard key is configured
- `ShowKeyModal` is false when no dashboard key is configured

---

## 2. Edge Cases

### Edge Case 2.1: Empty Key Input
- User submits an empty key value
- System should treat empty key as invalid when a dashboard key is configured

### Edge Case 2.2: No Key Configured in Settings
- Administrator has not set a dashboard key in configuration
- All users should have full access without any modal prompt

### Edge Case 2.3: Key Changed While Session Active
- User has valid key stored in localStorage
- Administrator changes the dashboard key in configuration
- User should be prompted to re-enter the new key

### Edge Case 2.4: Malformed Key in Query
- User provides special characters or malformed string as key
- System should reject and treat as unauthenticated

### Edge Case 2.5: localStorage Unavailable
- Browser has localStorage disabled or blocked
- User cannot store the key persistently
- Modal should reappear on each page visit

### Edge Case 2.6: Concurrent Requests
- Multiple API requests sent simultaneously
- Each request should be independently authenticated
- No race conditions in authentication logic

### Edge Case 2.7: Browser Back/Forward Navigation
- User navigates away from dashboard and returns
- Previously entered key should be retrieved from localStorage
- Modal should not appear if stored key is still valid

### Edge Case 2.8: Key Expiration
- If session-based authentication with expiration is implemented
- Session should timeout appropriately
- User should be prompted for key again after expiration

---

## 3. What Constitutes Failure

### Failure Criteria 3.1: Data Leakage
- **Description:** Any client data, features, or stage groups sent to unauthenticated users
- **Test:** Inspect network response or page source for unauthenticated requests
- **Expected Error:** Empty arrays in response, no sensitive data present
- **Rollback:** None required - this is a critical security fix

### Failure Criteria 3.2: Blank Screen for Unauthenticated Users
- **Description:** Unauthenticated users see empty/blank page instead of modal
- **Test:** Load page without authentication and verify modal is visible
- **Expected Error:** Modal overlay should be visible immediately
- **Rollback:** Revert template changes to restore modal rendering

### Failure Criteria 3.3: Authentication Bypass
- **Description:** Users can access API endpoints without providing valid key
- **Test:** Make API calls without key parameter and observe response
- **Expected Error:** HTTP 401 status returned
- **Rollback:** Restore authentication checks in API handlers

### Failure Criteria 3.4: Incorrect Key Accepted
- **Description:** System accepts incorrect/invalid keys as valid
- **Test:** Provide wrong key and attempt to access dashboard
- **Expected Error:** User remains unauthenticated, modal remains visible
- **Rollback:** Verify key comparison logic in authentication handler

### Failure Criteria 3.5: Modal Shows When No Key Configured
- **Description:** Modal appears even when no dashboard key is set in configuration
- **Test:** Configure no dashboard key, load page
- **Expected Error:** No modal appears, full dashboard accessible
- **Rollback:** Fix conditional logic for `ShowKeyModal`

### Failure Criteria 3.6: JavaScript Errors Blocking Modal
- **Description:** JavaScript errors prevent modal from functioning
- **Test:** Open browser console, load page
- **Expected Error:** No JavaScript errors related to modal functionality
- **Rollback:** Fix JavaScript errors, ensure modal scripts load correctly

---

## 4. Out of Scope

The following items are explicitly NOT tested:

### UI/Visual Testing
- Visual assertions on modal colors, fonts, or styling
- Animation timing or transition effects
- Responsive design verification across different screen sizes
- Cross-browser visual compatibility (CSS rendering)

### Performance Testing
- Page load time benchmarks
- Memory usage profiling
- API response time measurements
- Concurrent user load testing

### Timing-Specific Tests
- Exact timing of modal appearance after page load
- Animation duration verification
- Transition delays between states
- JavaScript execution timing

### External Integrations
- Third-party authentication providers
- OAuth or SSO integration (not part of this feature)
- External API rate limiting

### Long-Term Persistence
- Key expiration policies beyond session
- Database cleanup of old sessions
- Key rotation mechanisms

### Mobile-Specific Behaviors
- Touch event handling on mobile devices
- Mobile browser localStorage behavior differences
- Mobile viewport rendering

### Accessibility (Detail Level)
- Screen reader compatibility beyond basic functionality
- ARIA label verification
- Keyboard navigation flow (basic flow acceptable)
- Color contrast ratios

### Network Conditions
- Slow network simulation
- Offline behavior
- Connection retry logic

---

## Summary

This test specification focuses on verifying the core security and functionality requirements:

1. **Modal must render for unauthenticated users** - No blank screens
2. **No data leakage to unauthenticated clients** - Server-side enforcement
3. **Key validation works correctly** - Accept valid, reject invalid
4. **Modal only appears when key is configured** - Conditional display
5. **API endpoints protected** - 401 for unauthenticated requests

The test approach prioritizes security-critical behaviors over visual or performance aspects.
