# Block Association Submission System - Integration Guide

## Overview
This guide will help you set up a system where users can submit block associations through a Google Form, which stores data in Google Sheets for your review, and then you can add approved submissions to your Mapbox dataset.

---

## Step 1: Create Google Form

### 1.1 Create a New Google Form
1. Go to [Google Forms](https://forms.google.com)
2. Click "Blank" to create a new form
3. Title it: "Bed Stuy Block Association Submission"

### 1.2 Add These Form Fields

**Required Fields:**
- **Association Name** (Short answer)
  - Question: "What is the name of your block association?"
  - Make required: Yes

- **Street Location** (Short answer)
  - Question: "What street(s) does your block association cover? (e.g., Decatur Street between Marcus Garvey Blvd and Stuyvesant Ave)"
  - Make required: Yes

- **Starting Address** (Short answer)
  - Question: "Starting street address/intersection"
  - Make required: Yes

- **Ending Address** (Short answer)
  - Question: "Ending street address/intersection"
  - Make required: Yes

**Optional Contact Information Fields:**
- **President Name** (Short answer)
- **Contact Email** (Email field)
- **Phone Number** (Short answer)
- **Website** (Short answer)
- **Meeting Schedule** (Paragraph)
  - Question: "When and where does your block association meet?"
- **Year Founded** (Short answer)

**Submitter Information:**
- **Your Name** (Short answer) - Required
- **Your Email** (Email) - Required
- **Your Role** (Multiple choice)
  - Options: President, Board Member, Member, Community Member

### 1.3 Configure Form Settings
1. Click the gear icon (Settings)
2. Under "Responses":
   - ✓ Collect email addresses
   - ✓ Limit to 1 response (optional)
   - ✓ Send respondents a copy of their response
3. Click "Save"

### 1.4 Get Your Form URL
1. Click "Send" button at top right
2. Click the link icon (🔗)
3. Click "Shorten URL"
4. Copy this URL - you'll use it in Step 2

---

## Step 2: Connect Form to Google Sheets

### 2.1 Link to Sheets
1. In your Google Form, click the "Responses" tab
2. Click the Google Sheets icon (green spreadsheet)
3. Select "Create a new spreadsheet"
4. Name it: "Block Association Submissions"
5. Click "Create"

### 2.2 Add Review Columns
Your sheet will auto-populate with submission data. Add these columns to the right:

- **Status** (for tracking: Pending/Approved/Rejected)
- **Geocoded Start** (for latitude/longitude of start point)
- **Geocoded End** (for latitude/longitude of end point)
- **Notes** (for any additional comments)
- **Date Added to Map** (date when added to Mapbox)

---

## Step 3: Update Your HTML File

Replace `'YOUR_GOOGLE_FORM_URL'` in your index.html file (line 831) with your actual Google Form URL from Step 1.4.

Example:
```javascript
window.open('https://forms.gle/abc123xyz', '_blank');
```

---

## Step 4: Workflow for Adding Data to Mapbox

### Option A: Manual Process (Recommended for Starting)

**For Each Approved Submission:**

1. **Review submission** in Google Sheets
2. **Geocode the addresses**:
   - Use [Google Maps](https://maps.google.com) to get coordinates
   - Search for the starting address, right-click on map → "What's here?"
   - Copy latitude, longitude (e.g., 40.6872, -73.9351)
   - Repeat for ending address
   - Add to "Geocoded Start" and "Geocoded End" columns

3. **Add to Mapbox**:

   **Method 1: Mapbox Studio Dataset Editor**
   - Go to [Mapbox Studio](https://studio.mapbox.com/)
   - Navigate to your dataset
   - Click "Edit in dataset editor"
   - Draw a line between start and end points
   - Add properties (match your existing property names):
     - `Event.Name`: Association name
     - `Event.Location`: Street location
     - `Event.ID`: Generate a unique number (check highest existing ID + 1)
   - Save the dataset
   - Update your tileset

   **Method 2: Upload GeoJSON**
   - Create a GeoJSON file with new features
   - Upload to Mapbox dataset
   - Update tileset

4. **Update Google Sheet**:
   - Mark "Status" as "Approved"
   - Add date to "Date Added to Map"

### Option B: Semi-Automated with Google Apps Script

For more automation, you can create a Google Apps Script that:
1. Geocodes addresses automatically using Google Maps API
2. Exports approved submissions as GeoJSON
3. You manually upload the GeoJSON to Mapbox

**Would you like me to provide the Apps Script code for this?**

---

## Step 5: Optional Enhancements

### 5.1 Add Map-Based Submission
Instead of text addresses, users could click on the map to define their block:

1. Add a "Draw Your Block" mode to the map
2. Users click start/end points on the map
3. Coordinates are automatically captured
4. Pre-fill the Google Form with coordinates

**Would you like me to implement this feature?**

### 5.2 Display Pending Submissions
Show pending/unverified submissions on the map in a different color:

1. Export Google Sheet as CSV
2. Host CSV publicly (or use Google Sheets API)
3. Load pending submissions onto map with different styling
4. Add "Unverified" label

---

## Step 6: Testing

1. **Test the form**:
   - Click "Add Your Association" button
   - Fill out the form
   - Submit

2. **Check Google Sheets**:
   - Verify submission appears
   - Test your review workflow

3. **Add test data to Mapbox**:
   - Practice adding one feature
   - Verify it appears on the map

---

## Important Notes

### Data Privacy
- Collect only necessary information
- Add privacy notice to your form
- Store submitter emails securely
- Consider GDPR/privacy law compliance

### Data Quality
- Review all submissions before adding to map
- Verify street boundaries are accurate
- Check for duplicate submissions
- Maintain consistent naming conventions

### Maintenance
- Check for new submissions regularly
- Set up email notifications for new submissions:
  - In Google Form → Settings → Responses → Get email notifications

---

## Quick Reference: Your Current Data Structure

Your Mapbox layer uses these properties:
- `Event.ID` - Unique identifier
- `Event.Name` - Association name (includes year)
- `Event.Location` - Street location description

When adding new data, ensure you match this structure exactly.

---

## Next Steps

1. ✅ Create your Google Form using the template above
2. ✅ Link it to Google Sheets
3. ✅ Update the URL in your index.html
4. ✅ Test the submission process
5. ✅ Practice adding one approved submission to Mapbox

**Questions or need help with any step? Let me know!**

---

## Alternative: Fully Automated System (Advanced)

If you want a fully automated system where approved submissions automatically appear on the map, you would need:

1. **Google Sheets API** - to read approved submissions
2. **Mapbox Datasets API** - to programmatically add features
3. **Backend server** (Node.js, Python, etc.) - to handle the automation
4. **Authentication** - to protect your Mapbox API tokens

This is more complex but possible. Would you like guidance on this approach instead?
