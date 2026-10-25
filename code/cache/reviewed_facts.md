# Reviewed screenshot facts

Hand-verified facts for the 11 PNG screenshots. Each entry is bound to the image's SHA-256;
if an image changes, its entry is stale and the run stops until it is reviewed again.
Never edit `llm_cache.json`. No per-request intent decisions belong here.

## Changelog

- 2026-10-25: All 11 model transcriptions compared visually against their PNGs by Claude
  `claude-opus-5-5`, on the user's instruction. No transcription errors found; facts
  approved unchanged. Known limits noted at review: some observations use a displayed name
  as `resource_id` (evidence_04, 19, 28, 42, 46) rather than the catalog resource ID; that
  mapping is handled in code, not by editing facts. evidence_60 shows Soo-jin Park's
  identifiers under `marketing_001`.

### Uncertainty resolutions (2026-10-25 review)

Every model uncertainty was resolved as follows; facts stay exactly as displayed.

- No ISO observation time or replica visible: correct; `observed_at` and `copy` stay null and the file's manifest `captured_at` applies.
- Dates or times shown without a year or date: kept as displayed; no components are inferred.
- Truncated checksums or fingerprints (evidence_23, 37, 46): kept as displayed; not used as identifiers.
- Em dashes in evidence_09 (search_1874) and evidence_19: literal display values, not a redacted or absent state.
- evidence_14 shows 12 connectors but 4 rows: only the 4 visible rows are facts.
- evidence_28 `support_1034` header reference: visible next to CS-5034 and the customer; it is the support record ID the catalog uses.
- evidence_32 activity list: restore rows are recorded under the backups they name; the checksum row stays on the selected backup_9287.
- Integrity, signature, status and health labels: interface claims, kept as displayed.
- "Human review required": done (this review).

```json
{
  "version": 1,
  "images": [
    {
      "file_id": "evidence_04",
      "sha256": "d5c32ca5b6e1a2b4bbe9f5d41835ef1fd5d4f7061dab9067a4f9f88cea3c1861",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "user_11",
            "system": "Customer Operations",
            "collection": "Customers",
            "copy": null,
            "observed_at": null,
            "fields": {
              "full_name": "Haruka Sato",
              "user_id": "user_11",
              "account_id": "account_5762",
              "email": "haruka.s1838@zoho.com",
              "phone": "+81 90 7610 2507",
              "signup_channel": "mobile_app",
              "market": "JP",
              "preferred_language": "Japanese",
              "last_contact": "24 Sep 2026 09:14 JST",
              "default_address": "2-18-4 Kichijoji, Musashino, Tokyo 180-0004",
              "loyalty_tier": "Silver",
              "support": "CS-8538 · support_9538",
              "member_since": "12 Jan 2024"
            },
            "source": "Haruka Sato\nSilver\nuser_11 · account_5762\nEmail\nharuka.s1838@zoho.com\nPhone\n+81 90 7610 2507\nSignup channel\nmobile_app\nMarket\nJP\nPreferred language\nJapanese\nLast contact\n24 Sep 2026 09:14 JST\nCustomer details\nDefault address\n2-18-4 Kichijoji, Musashino, Tokyo\n180-0004\nLoyalty tier\nSilver\nSupport\nCS-8538 · support_9538\nMember since\n12 Jan 2024"
          },
          {
            "resource_id": "ORD-20265762",
            "system": "Customer Operations",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "order_id": "ORD-20265762",
              "date": "12 Sep 2026",
              "item": "Ceramic coffee set",
              "qty": 1,
              "total": "JPY 8,900",
              "status": "Delivered · 18 Sep",
              "activity_date": "18 Sep",
              "activity": "Order ORD-20265762 delivered"
            },
            "source": "Order ID\nDate\nItem\nQty\nTotal\nStatus\nORD-20265762\n12 Sep 2026\nCeramic coffee set\n1\nJPY 8,900\nDelivered · 18 Sep\n18 Sep\nOrder ORD-20265762 delivered"
          },
          {
            "resource_id": "ORD-20265518",
            "system": "Customer Operations",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "order_id": "ORD-20265518",
              "date": "28 Aug 2026",
              "item": "Travel backpack",
              "qty": 1,
              "total": "JPY 12,400",
              "status": "Returned · 03 Sep",
              "activity_date": "03 Sep",
              "activity": "Order ORD-20265518 returned"
            },
            "source": "Order ID\nDate\nItem\nQty\nTotal\nStatus\nORD-20265518\n28 Aug 2026\nTravel backpack\n1\nJPY 12,400\nReturned · 03 Sep\n03 Sep\nOrder ORD-20265518 returned"
          },
          {
            "resource_id": "support_9538",
            "system": "Customer Operations",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "support_reference": "CS-8538",
              "created_activity_date": "28 Aug",
              "created_activity": "support_9538 created"
            },
            "source": "Support\nCS-8538 · support_9538\n28 Aug\nsupport_9538 created"
          },
          {
            "resource_id": "CS-8538",
            "system": "Customer Operations",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "support_reference": "support_9538",
              "activity_date": "24 Sep",
              "activity": "Customer replied to CS-8538"
            },
            "source": "Support\nCS-8538 · support_9538\n24 Sep\nCustomer replied to CS-8538"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_09",
      "sha256": "75afdb0ae53a4510c83399a70d88073cb4fb2747454095756e4ddad1a8c70605",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "search_428",
            "system": "Search Console",
            "collection": "customer_directory_v3",
            "copy": null,
            "observed_at": null,
            "fields": {
              "display_name": "Gabriel Silva",
              "email": "g.silva76@gmail.com",
              "customer_reference": "user_13",
              "score": 9.84,
              "last_indexed": "05:58:21 UTC",
              "indexed_at": "2026-09-01T05:58:21Z",
              "environment": "prod-eu",
              "executed": "01 Sep 2026 06:00:14 UTC"
            },
            "source": "Search Console\ncustomer_directory_v3 / prod-eu\nExecuted\n01 Sep 2026 06:00:14 UTC\n_id\nDisplay name\nEmail\nCustomer reference\nScore\nLast indexed\nsearch_428\nGabriel Silva\ng.silva76@gmail.com\nuser_13\n9.84\n05:58:21 UTC\nRaw document · search_428\n_index customer_directory_v3\n_id search_428\ndisplay_name Gabriel Silva\nemail g.silva76@gmail.com\ncustomer_reference user_13\nindexed_at 2026-09-01T05:58:21Z"
          },
          {
            "resource_id": "search_1874",
            "system": "Search Console",
            "collection": "customer_directory_v3",
            "copy": null,
            "observed_at": null,
            "fields": {
              "display_name": "—",
              "email": "g.silva76.jr@gmail.com",
              "customer_reference": "—",
              "score": 4.12,
              "last_indexed": "05:55:03 UTC",
              "environment": "prod-eu",
              "executed": "01 Sep 2026 06:00:14 UTC"
            },
            "source": "Search Console\ncustomer_directory_v3 / prod-eu\nExecuted\n01 Sep 2026 06:00:14 UTC\n_id\nDisplay name\nEmail\nCustomer reference\nScore\nLast indexed\nsearch_1874\n—\ng.silva76.jr@gmail.com\n—\n4.12\n05:55:03 UTC"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_14",
      "sha256": "e81fabc93ea1b5f4c918d934cdb2bea05d61737adfcc29f281494f5d8ec46355",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "data_flow_1121",
            "system": "BridgeOps",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "destination": "customer_analytics_warehouse",
              "selector": "jain.aditi@gmail.com",
              "mode": "Continuous",
              "events_per_hour": "1,284",
              "last_contact": "07:59:42",
              "status": "Healthy",
              "owner": "Data Platform",
              "sync_mode": "Continuous sync",
              "last_response": "200 OK · 1.8 s",
              "activity_07_59_42": "124 events delivered",
              "activity_07_58_10": "checkpoint committed",
              "activity_07_56_44": "18 events retried",
              "activity_07_55_02": "destination acknowledged batch"
            },
            "source": "data_flow_1121\ncustomer_analytics_warehouse\njain.aditi@gmail.com\nContinuous\n1,284\n07:59:42\nStatus\nHealthy\nOwner\nData Platform\nDestination\ncustomer_analytics_warehouse\nSelector\njain.aditi@gmail.com\nSync mode\nContinuous sync\nLast response\n200 OK · 1.8 s\nActivity\n07:59:42\n124 events delivered\n07:58:10\ncheckpoint committed\n07:56:44\n18 events retried\n07:55:02\ndestination acknowledged batch"
          },
          {
            "resource_id": "data_flow_9846",
            "system": "BridgeOps",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "destination": "customer_analytics_warehouse",
              "selector": "lucia-torres@gmail.com",
              "mode": "Continuous",
              "events_per_hour": "842",
              "last_contact": "07:59:36"
            },
            "source": "data_flow_9846\ncustomer_analytics_warehouse\nlucia-torres@gmail.com\nContinuous\n842\n07:59:36"
          },
          {
            "resource_id": "Order notifications",
            "system": "BridgeOps",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "destination": "notification_service",
              "selector": "order events",
              "mode": "Continuous",
              "events_per_hour": "650",
              "last_contact": "07:59:28"
            },
            "source": "Order notifications\nnotification_service\norder events\nContinuous\n650\n07:59:28"
          },
          {
            "resource_id": "Delivery tracking",
            "system": "BridgeOps",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "destination": "logistics_warehouse",
              "selector": "shipment events",
              "mode": "Continuous",
              "events_per_hour": "420",
              "last_contact": "07:59:13"
            },
            "source": "Delivery tracking\nlogistics_warehouse\nshipment events\nContinuous\n420\n07:59:13"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_19",
      "sha256": "4e4cb259bc2010b622b0c53fe5872258f994fefc23452a83bb27473758693484",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "full_name": "Omar Darwish",
              "user_id": "user_20",
              "analytics_id": "analytics_7438",
              "email": "darwish.omar@icloud.com",
              "date_range": "Last 90 days",
              "sessions": 12,
              "products_viewed": 19,
              "completed_orders": 2,
              "revenue": "USD 167.42",
              "mobile_desktop": "67% / 33%",
              "acquisition": "Organic search",
              "market": "AE",
              "audience": "Returning shopper",
              "interest": "Coffee & dining",
              "last_activity": "13 Jul 2026 14:22 UTC",
              "first_seen": "21 Apr 2026 09:14 UTC",
              "timeline_update": "Updated moments ago · 5 records"
            },
            "source": "Northstar Shop / Pulse Analytics / Users\nOmar Darwish\nuser_20 · analytics_7438 · darwish.omar@icloud.com\nLast 90 days\nSessions\n12\nProducts viewed\n19\nCompleted orders\n2\nRevenue\nUSD 167.42\nMobile / Desktop\n67% / 33%\nAcquisition\nOrganic search\nProfile attributes\nMarket\nAE\nAudience\nReturning shopper\nInterest\nCoffee & dining\nLast activity\n13 Jul 2026 14:22 UTC\nFirst seen\n21 Apr 2026 09:14 UTC\nUpdated moments ago · 5 records"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "row_number": 1,
              "time": "14:22",
              "event": "Order completed",
              "product_detail": "Ceramic coffee set",
              "value": "USD 84.21",
              "device": "Mobile"
            },
            "source": "Time\nEvent\nProduct / detail\nValue\nDevice\n1\n14:22\nOrder completed\nCeramic coffee set\nUSD 84.21\nMobile"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "row_number": 2,
              "time": "14:20",
              "event": "Checkout started",
              "product_detail": "2 items",
              "value": "USD 84.21",
              "device": "Mobile"
            },
            "source": "2\n14:20\nCheckout started\n2 items\nUSD 84.21\nMobile"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "row_number": 3,
              "time": "14:17",
              "event": "Product viewed",
              "product_detail": "Ceramic coffee set",
              "value": "—",
              "device": "Mobile"
            },
            "source": "3\n14:17\nProduct viewed\nCeramic coffee set\n—\nMobile"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "row_number": 4,
              "time": "14:16",
              "event": "Added to cart",
              "product_detail": "Ceramic coffee set",
              "value": "USD 42.00",
              "device": "Mobile"
            },
            "source": "4\n14:16\nAdded to cart\nCeramic coffee set\nUSD 42.00\nMobile"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "row_number": 5,
              "time": "14:12",
              "event": "Product viewed",
              "product_detail": "Stoneware mug",
              "value": "USD 18.00",
              "device": "Desktop"
            },
            "source": "5\n14:12\nProduct viewed\nStoneware mug\nUSD 18.00\nDesktop"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "activity_date": "13 Jul",
              "activity": "6 events in active session"
            },
            "source": "Activity\n13 Jul\n6 events in active session"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "activity_date": "08 Jul",
              "activity": "Order completed · USD 83.21"
            },
            "source": "08 Jul\nOrder completed · USD 83.21"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "activity_date": "30 Jun",
              "activity": "Product search · coffee set"
            },
            "source": "30 Jun\nProduct search · coffee set"
          },
          {
            "resource_id": "user_20",
            "system": "Pulse Analytics",
            "collection": "Users",
            "copy": null,
            "observed_at": null,
            "fields": {
              "activity_date": "22 Jun",
              "activity": "Campaign opened"
            },
            "source": "22 Jun\nCampaign opened"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_23",
      "sha256": "18b95c1bd8b8956e40216df935498a9f3c181ea2a69b5cd6d3c0841a816d5557",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "export_1261",
            "system": "Customer Download Center",
            "collection": "Data exports",
            "copy": null,
            "observed_at": null,
            "fields": {
              "filename": "customer_export_1261.zip",
              "owner": "marcus.lee@northstar.shop",
              "status": "Collecting",
              "created": "20 Jun 14:18 UTC"
            },
            "source": "Export ID\nFilename\nOwner\nStatus\nCreated\nexport_1261\ncustomer_export_1261.zip\nmarcus.lee@northstar.shop\nCollecting\n20 Jun 14:18 UTC"
          },
          {
            "resource_id": "export_1260",
            "system": "Customer Download Center",
            "collection": "Data exports",
            "copy": null,
            "observed_at": null,
            "fields": {
              "filename": "customer_export_1260.zip",
              "owner": "ingrid.dahl@outlook.com",
              "status": "Ready",
              "created_table": "18 Jun 06:42 UTC",
              "created": "18 Jun 2026 06:42 UTC",
              "expires": "25 Jun 2026 06:42 UTC",
              "size": "18.4 MB",
              "checksum": "SHA-256 · 41ca09...",
              "download_count": 0,
              "included": "Profile · Orders · Saved addresses",
              "activity_06_46": "Ready for download",
              "activity_06_45": "Package created",
              "activity_06_43": "Collecting customer records",
              "activity_06_42": "Export requested"
            },
            "source": "Export ID\nFilename\nOwner\nStatus\nCreated\nexport_1260\ncustomer_export_1260.zip\ningrid.dahl@outlook.com\nReady\n18 Jun 06:42 UTC\nexport_1260\nOwner\ningrid.dahl@outlook.com\nCreated\n18 Jun 2026 06:42 UTC\nExpires\n25 Jun 2026 06:42 UTC\nSize\n18.4 MB\nChecksum\nSHA-256 · 41ca09...\nDownload count\n0\nIncluded\nProfile · Orders · Saved addresses\nActivity\n06:46\nReady for download\n06:45\nPackage created\n06:43\nCollecting customer records\n06:42\nExport requested"
          },
          {
            "resource_id": "export_1259",
            "system": "Customer Download Center",
            "collection": "Data exports",
            "copy": null,
            "observed_at": null,
            "fields": {
              "filename": "customer_export_1259.zip",
              "owner": "sara.kim@northstar.shop",
              "status": "Packaging",
              "created": "17 Jun 11:03 UTC"
            },
            "source": "Export ID\nFilename\nOwner\nStatus\nCreated\nexport_1259\ncustomer_export_1259.zip\nsara.kim@northstar.shop\nPackaging\n17 Jun 11:03 UTC"
          },
          {
            "resource_id": "export_1258",
            "system": "Customer Download Center",
            "collection": "Data exports",
            "copy": null,
            "observed_at": null,
            "fields": {
              "filename": "customer_export_1258.zip",
              "owner": "daniel.park@northstar.shop",
              "status": "Ready",
              "created": "16 Jun 09:27 UTC"
            },
            "source": "Export ID\nFilename\nOwner\nStatus\nCreated\nexport_1258\ncustomer_export_1258.zip\ndaniel.park@northstar.shop\nReady\n16 Jun 09:27 UTC"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_28",
      "sha256": "542b7aec17e6b2f38827896e7eb52c70e4bfaaca22433621be1f80f54884e944",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "CS-5034",
            "system": "Helpline Console",
            "collection": "Inbox",
            "copy": null,
            "observed_at": null,
            "fields": {
              "subject": "Where is my delivery?",
              "header_reference": "support_1034",
              "full_name": "Kofi Agyeman",
              "priority": "Normal priority",
              "sla": "01:42",
              "email": "kagyeman8947@fastmail.com",
              "assigned": "Maya Chen",
              "tags": "Delivery · Tracking · Order update",
              "conversation_update": "Updated moments ago",
              "conversation_record_count": 4,
              "message_1_time": "09:12",
              "message_1_sender": "Kofi Agyeman",
              "message_1_text": "Could you let me know where my order is and when it is expected to arrive?",
              "message_2_time": "09:13",
              "message_2_sender": "Northstar Bot",
              "message_2_text": "We received your message and are checking the order details.",
              "message_3_time": "09:28",
              "message_3_sender": "Maya Chen",
              "message_3_text": "The parcel reached the regional hub and is on track for delivery on 26 May.",
              "message_4_time": "09:35",
              "message_4_sender": "Kofi Agyeman",
              "message_4_text": "Thanks for the update."
            },
            "source": "Northstar Shop / Helpline Console / Inbox\nCS-5034 · Where is my delivery?\nSLA 01:42\nsupport_1034 · Kofi Agyeman · Normal priority\nCustomer\nKofi Agyeman\nEmail\nkagyeman8947@fastmail.com\nAssigned\nMaya Chen\nTags\nDelivery · Tracking · Order update\nConversation\nUpdated moments ago · 4 records\nTime\nSender\nMessage\n1\n09:12\nKofi Agyeman\nCould you let me know where my order is and when it is expected to arrive?\n2\n09:13\nNorthstar Bot\nWe received your message and are checking the order details.\n3\n09:28\nMaya Chen\nThe parcel reached the regional hub and is on track for delivery on 26 May.\n4\n09:35\nKofi Agyeman\nThanks for the update."
          },
          {
            "resource_id": "ORD-20265034",
            "system": "Helpline Console",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "product": "Running shoes",
              "status": "In transit",
              "carrier": "SwiftShip",
              "tracking": "SWF-884103",
              "expected": "26 May 2026",
              "assigned_agent": "Maya Chen",
              "activity_1_date": "22 May",
              "activity_1_event": "Order confirmed",
              "activity_2_date": "23 May",
              "activity_2_event": "Dispatched",
              "activity_3_date_time": "24 May 07:46",
              "activity_3_event": "Reached regional hub",
              "activity_4_date_time": "24 May 08:15",
              "activity_4_event": "In transit",
              "activity_5_date": "26 May",
              "activity_5_event": "Delivery expected"
            },
            "source": "Order ORD-20265034\nProduct\nRunning shoes\nStatus\nIn transit\nCarrier\nSwiftShip\nTracking\nSWF-884103\nExpected\n26 May 2026\nAssigned agent\nMaya Chen\nActivity\n22 May\nOrder confirmed\n23 May\nDispatched\n24 May 07:46\nReached regional hub\n24 May 08:15\nIn transit\n26 May\nDelivery expected"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_32",
      "sha256": "2c246aa5843bec609b9ddfdf1154cd81e0fd61996f83954735e9856e48c18bf3",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "backup_9291",
            "system": "Vault Recovery Manager",
            "collection": "Customer-platform archives",
            "copy": null,
            "observed_at": null,
            "fields": {
              "account": "bluepeakco@outlook.com",
              "region": "ap-southeast-1",
              "created": "26 Apr 02:00",
              "size": "102.3 GB",
              "encryption": "AES-256",
              "tier": "Archive"
            },
            "source": "Archive ID\nAccount\nRegion\nCreated\nSize\nEncryption\nTier\nbackup_9291\nbluepeakco@outlook.com\nap-southeast-1\n26 Apr 02:00\n102.3 GB\nAES-256\nArchive"
          },
          {
            "resource_id": "backup_9288",
            "system": "Vault Recovery Manager",
            "collection": "Customer-platform archives",
            "copy": null,
            "observed_at": null,
            "fields": {
              "account": "miller.logistics@company.com",
              "region": "us-west-2",
              "created": "26 Apr 02:00",
              "size": "67.1 GB",
              "encryption": "AES-256",
              "tier": "Archive"
            },
            "source": "Archive ID\nAccount\nRegion\nCreated\nSize\nEncryption\nTier\nbackup_9288\nmiller.logistics@company.com\nus-west-2\n26 Apr 02:00\n67.1 GB\nAES-256\nArchive"
          },
          {
            "resource_id": "backup_9287",
            "system": "Vault Recovery Manager",
            "collection": "Customer-platform archives",
            "copy": null,
            "observed_at": null,
            "fields": {
              "account": "areed1433@gmail.com",
              "region": "eu-west-1",
              "created": "26 Apr 02:00",
              "size": "84.7 GB",
              "encryption": "AES-256",
              "tier": "Archive",
              "path": "s3://northstar-backups/customer-platform/weekly/segment-42-20260426.tar.zst",
              "created_detail": "26 Apr 2026 02:00 UTC",
              "checksum": "Verified",
              "last_drill": "29 Mar 2026",
              "retention_end": "26 Jul 2026",
              "activity_date": "22 Apr",
              "activity": "checksum verification",
              "activity_result": "Completed"
            },
            "source": "Archive ID\nAccount\nRegion\nCreated\nSize\nEncryption\nTier\nbackup_9287\nareed1433@gmail.com\neu-west-1\n26 Apr 02:00\n84.7 GB\nAES-256\nArchive\nbackup_9287\nAccount\nareed1433@gmail.com\nPath\ns3://northstar-backups/customer-\nplatform/weekly/segment-42-\n20260426.tar.zst\nRegion\neu-west-1\nCreated\n26 Apr 2026 02:00 UTC\nEncryption\nAES-256\nChecksum\nVerified\nLast drill\n29 Mar 2026\nRetention end\n26 Jul 2026\nActivity\n22 Apr\nchecksum verification · Completed"
          },
          {
            "resource_id": "backup_9286",
            "system": "Vault Recovery Manager",
            "collection": "Customer-platform archives",
            "copy": null,
            "observed_at": null,
            "fields": {
              "account": "summit-retail@company.com",
              "region": "us-east-1",
              "created": "26 Apr 02:00",
              "size": "91.4 GB",
              "encryption": "AES-256",
              "tier": "Archive",
              "activity_date": "25 Apr",
              "activity": "restored",
              "activity_result": "Completed"
            },
            "source": "Archive ID\nAccount\nRegion\nCreated\nSize\nEncryption\nTier\nbackup_9286\nsummit-retail@company.com\nus-east-1\n26 Apr 02:00\n91.4 GB\nAES-256\nArchive\nActivity\n25 Apr\nbackup_9286 restored · Completed"
          },
          {
            "resource_id": "backup_9281",
            "system": "Vault Recovery Manager",
            "collection": "Customer-platform archives",
            "copy": null,
            "observed_at": null,
            "fields": {
              "activity_date": "24 Apr",
              "activity": "restored",
              "activity_result": "Completed"
            },
            "source": "Activity\n24 Apr\nbackup_9281 restored · Completed"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_37",
      "sha256": "ca59f693f4e7002466b3fdeaf1bb6763618770963dd811b60003772d07b4b03b",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "audit_record_003",
            "system": "Sentinel",
            "collection": "Account events",
            "copy": null,
            "observed_at": null,
            "fields": {
              "time_utc": "25 Aug 04:03",
              "event": "Account closure recorded",
              "category": "Account lifecycle",
              "subject_reference": "user_03",
              "actor": "Automated workflow",
              "integrity": "Valid",
              "event_type": "account_closure_recorded",
              "raw_event_actor": "automated account workflow",
              "recorded_at": "2026-08-25T04:03:00Z",
              "fingerprint": "8f0c...31ad",
              "signature": "Valid",
              "journal": "2026-Q3"
            },
            "source": "25 Aug 04:03\nAccount closure recorded\nAccount lifecycle\nuser_03\nAutomated workflow\nValid\naudit_record_003\naudit_record_003 · Raw event\nevent_type\naccount_closure_recorded\nsubject\nuser_03\nactor\nautomated account workflow\nrecorded_at\n2026-08-25T04:03:00Z\nfingerprint\n8f0c...31ad\nsignature\nValid\njournal\n2026-Q3"
          },
          {
            "resource_id": "audit_record_002",
            "system": "Sentinel",
            "collection": "Account events",
            "copy": null,
            "observed_at": null,
            "fields": {
              "time_utc": "25 Aug 03:21",
              "event": "Session revoked",
              "category": "Session management",
              "subject_reference": "user_03",
              "actor": "Security system",
              "integrity": "Valid"
            },
            "source": "25 Aug 03:21\nSession revoked\nSession management\nuser_03\nSecurity system\nValid\naudit_record_002"
          },
          {
            "resource_id": "audit_record_001",
            "system": "Sentinel",
            "collection": "Account events",
            "copy": null,
            "observed_at": null,
            "fields": {
              "time_utc": "24 Aug 16:14",
              "event": "Recovery challenge completed",
              "category": "Authentication",
              "subject_reference": "user_03",
              "actor": "User (self)",
              "integrity": "Valid"
            },
            "source": "24 Aug 16:14\nRecovery challenge completed\nAuthentication\nuser_03\nUser (self)\nValid\naudit_record_001"
          },
          {
            "resource_id": "audit_record_006",
            "system": "Sentinel",
            "collection": "Account events",
            "copy": null,
            "observed_at": null,
            "fields": {
              "time_utc": "22 Aug 11:27",
              "event": "Email change confirmed",
              "category": "Profile update",
              "subject_reference": "user_03",
              "actor": "User (self)",
              "integrity": "Valid"
            },
            "source": "22 Aug 11:27\nEmail change confirmed\nProfile update\nuser_03\nUser (self)\nValid\naudit_record_006"
          },
          {
            "resource_id": "audit_record_005",
            "system": "Sentinel",
            "collection": "Account events",
            "copy": null,
            "observed_at": null,
            "fields": {
              "time_utc": "20 Aug 09:42",
              "event": "Login from new device",
              "category": "Authentication",
              "subject_reference": "user_03",
              "actor": "User (self)",
              "integrity": "Valid"
            },
            "source": "20 Aug 09:42\nLogin from new device\nAuthentication\nuser_03\nUser (self)\nValid\naudit_record_005"
          },
          {
            "resource_id": "audit_record_004",
            "system": "Sentinel",
            "collection": "Account events",
            "copy": null,
            "observed_at": null,
            "fields": {
              "time_utc": "18 Aug 14:05",
              "event": "Password changed",
              "category": "Authentication",
              "subject_reference": "user_03",
              "actor": "User (self)",
              "integrity": "Valid"
            },
            "source": "18 Aug 14:05\nPassword changed\nAuthentication\nuser_03\nUser (self)\nValid\naudit_record_004"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_42",
      "sha256": "68c28a72afd6ed184d5990d15c543f8a516face204f0360f732755a8a63c28be",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "user_17",
            "system": "Customer Reference Registry",
            "collection": "Customer references",
            "copy": null,
            "observed_at": null,
            "fields": {
              "status": "Retained reference",
              "reference_type": "Customer key",
              "owner_team": "Account Platform",
              "region": "eu-west",
              "created": "11 Jan 2024 08:42 UTC",
              "most_recent_update": "10 Mar 2026 07:18 UTC",
              "record_count": 4,
              "metadata_reviewed_date": "10 Mar 2026",
              "finance_link_added_date": "22 Aug 2024",
              "support_link_added_date_1": "03 Jun 2024",
              "support_link_added_date_2": "14 Feb 2024",
              "reference_created_date": "11 Jan 2024"
            },
            "source": "user_17\nRetained reference\nCustomer key · Account Platform · eu-west\nReference type\nCustomer key\nCreated\n11 Jan 2024 08:42 UTC\nMost recent update\n10 Mar 2026 07:18 UTC\nOwner team\nAccount Platform\nUpdated moments ago · 4 records\nHistory\n10 Mar 2026 Metadata reviewed\n22 Aug 2024 Finance link added\n03 Jun 2024 Support link added\n14 Feb 2024 Support link added\n11 Jan 2024 Reference created"
          },
          {
            "resource_id": "ORD-20263245",
            "system": "Orders",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "relationship": "Linked to customer",
              "created_utc": "11 Jan 2024 08:45",
              "status": "Active",
              "activity": "Order link added"
            },
            "source": "System\nRecord reference\nRelationship\nCreated (UTC)\nStatus\nOrders\nORD-20263245\nLinked to customer\n11 Jan 2024 08:45\nActive\nOrder link added\nORD-20263245"
          },
          {
            "resource_id": "CS-3245",
            "system": "Support",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "relationship": "Linked to customer",
              "created_utc": "14 Feb 2024 12:16",
              "status": "Active",
              "activity": "Support links added"
            },
            "source": "System\nRecord reference\nRelationship\nCreated (UTC)\nStatus\nSupport\nCS-3245\nLinked to customer\n14 Feb 2024 12:16\nActive\nSupport links added\nCS-3245, CS-3378"
          },
          {
            "resource_id": "CS-3378",
            "system": "Support",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "relationship": "Linked to customer",
              "created_utc": "03 Jun 2024 09:27",
              "status": "Active",
              "activity": "Support links added"
            },
            "source": "System\nRecord reference\nRelationship\nCreated (UTC)\nStatus\nSupport\nCS-3378\nLinked to customer\n03 Jun 2024 09:27\nActive\nSupport links added\nCS-3245, CS-3378"
          },
          {
            "resource_id": "FIN-7742",
            "system": "Finance",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "relationship": "Linked to customer",
              "created_utc": "22 Aug 2024 15:03",
              "status": "Active",
              "activity": "Finance link added"
            },
            "source": "System\nRecord reference\nRelationship\nCreated (UTC)\nStatus\nFinance\nFIN-7742\nLinked to customer\n22 Aug 2024 15:03\nActive\nFinance link added\nFIN-7742"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_46",
      "sha256": "96e9a191380c29b9e0ab997007268c4cd6353cb6721ebd1ed4a32599d8d37e04",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "delivery_confirmation_19.pdf",
            "system": "Nimbus Object Storage",
            "collection": "northstar-customer-data",
            "copy": null,
            "observed_at": null,
            "fields": {
              "account": "Northstar Production",
              "region": "eu-west-1",
              "path": "eu-west-1 / support-attachments / 2026 / 02",
              "captured": "13 Feb 2026 08:00 UTC",
              "size": "684 KB",
              "modified": "12 Feb 2026 18:42 UTC",
              "storage_class": "Standard",
              "version": "v3",
              "content_type": "application/pdf",
              "encryption": "AES-256",
              "checksum": "e71b6d...",
              "uploader": "support-service",
              "activity_v3": "12 Feb 18:42 · metadata update",
              "activity_v2": "12 Feb 18:39 · upload complete",
              "activity_v1": "12 Feb 18:38 · object created"
            },
            "source": "Nimbus Object Storage\nnorthstar-customer-data\neu-west-1 / support-attachments / 2026 / 02\nAccount\nNorthstar Production\nRegion\neu-west-1\nCaptured\n13 Feb 2026 08:00 UTC\ndelivery_confirmation_19.pdf\n684 KB\n12 Feb 18:42\nStandard\nv3\nSize\n684 KB\nVersion\nv3\nContent type\napplication/pdf\nEncryption\nAES-256\nChecksum\ne71b6d...\nUploader\nsupport-service\nModified\n12 Feb 2026 18:42 UTC\nActivity\nv3\n12 Feb 18:42 · metadata update\nv2\n12 Feb 18:39 · upload complete\nv1\n12 Feb 18:38 · object created"
          },
          {
            "resource_id": "delivery_confirmation_20.pdf",
            "system": "Nimbus Object Storage",
            "collection": "northstar-customer-data",
            "copy": null,
            "observed_at": null,
            "fields": {
              "size": "712 KB",
              "modified_utc": "11 Feb 14:17",
              "storage_class": "Standard",
              "version": "v1"
            },
            "source": "Nimbus Object Storage\nnorthstar-customer-data\nName\nSize\nModified (UTC)\nStorage class\nVersion\ndelivery_confirmation_20.pdf\n712 KB\n11 Feb 14:17\nStandard\nv1"
          },
          {
            "resource_id": "return_label_4821.pdf",
            "system": "Nimbus Object Storage",
            "collection": "northstar-customer-data",
            "copy": null,
            "observed_at": null,
            "fields": {
              "size": "203 KB",
              "modified_utc": "10 Feb 09:33",
              "storage_class": "Standard",
              "version": "v1"
            },
            "source": "Nimbus Object Storage\nnorthstar-customer-data\nName\nSize\nModified (UTC)\nStorage class\nVersion\nreturn_label_4821.pdf\n203 KB\n10 Feb 09:33\nStandard\nv1"
          },
          {
            "resource_id": "return_label_4876.pdf",
            "system": "Nimbus Object Storage",
            "collection": "northstar-customer-data",
            "copy": null,
            "observed_at": null,
            "fields": {
              "size": "198 KB",
              "modified_utc": "11 Feb 16:05",
              "storage_class": "Standard",
              "version": "v2"
            },
            "source": "Nimbus Object Storage\nnorthstar-customer-data\nName\nSize\nModified (UTC)\nStorage class\nVersion\nreturn_label_4876.pdf\n198 KB\n11 Feb 16:05\nStandard\nv2"
          },
          {
            "resource_id": "invoice_NS-10493.pdf",
            "system": "Nimbus Object Storage",
            "collection": "northstar-customer-data",
            "copy": null,
            "observed_at": null,
            "fields": {
              "size": "318 KB",
              "modified_utc": "10 Feb 11:21",
              "storage_class": "Standard",
              "version": "v1"
            },
            "source": "Nimbus Object Storage\nnorthstar-customer-data\nName\nSize\nModified (UTC)\nStorage class\nVersion\ninvoice_NS-10493.pdf\n318 KB\n10 Feb 11:21\nStandard\nv1"
          },
          {
            "resource_id": "parcel_damage_7713.jpg",
            "system": "Nimbus Object Storage",
            "collection": "northstar-customer-data",
            "copy": null,
            "observed_at": null,
            "fields": {
              "size": "2.4 MB",
              "modified_utc": "12 Feb 10:08",
              "storage_class": "Standard",
              "version": "v1"
            },
            "source": "Nimbus Object Storage\nnorthstar-customer-data\nName\nSize\nModified (UTC)\nStorage class\nVersion\nparcel_damage_7713.jpg\n2.4 MB\n12 Feb 10:08\nStandard\nv1"
          },
          {
            "resource_id": "customer_export_2026-02-10.zip",
            "system": "Nimbus Object Storage",
            "collection": "northstar-customer-data",
            "copy": null,
            "observed_at": null,
            "fields": {
              "size": "128 MB",
              "modified_utc": "10 Feb 23:14",
              "storage_class": "Standard",
              "version": "v1"
            },
            "source": "Nimbus Object Storage\nnorthstar-customer-data\nName\nSize\nModified (UTC)\nStorage class\nVersion\ncustomer_export_2026-02-10.zip\n128 MB\n10 Feb 23:14\nStandard\nv1"
          },
          {
            "resource_id": "customer_export_2026-02-12.zip",
            "system": "Nimbus Object Storage",
            "collection": "northstar-customer-data",
            "copy": null,
            "observed_at": null,
            "fields": {
              "size": "131 MB",
              "modified_utc": "12 Feb 21:56",
              "storage_class": "Standard",
              "version": "v1"
            },
            "source": "Nimbus Object Storage\nnorthstar-customer-data\nName\nSize\nModified (UTC)\nStorage class\nVersion\ncustomer_export_2026-02-12.zip\n131 MB\n12 Feb 21:56\nStandard\nv1"
          }
        ],
        "uncertainties": []
      }
    },
    {
      "file_id": "evidence_60",
      "sha256": "51fa0e6a38f8fa3629ba1b0d0b90b4ed139715956b97bfd09a6f1a6232c15df5",
      "approved": true,
      "facts": {
        "observations": [
          {
            "resource_id": "marketing_001",
            "system": "Audience Operations",
            "collection": null,
            "copy": null,
            "observed_at": null,
            "fields": {
              "full_name": "Soo-jin Park",
              "email": "sj.park@naver.com",
              "phone": "+82 10-5837-9451",
              "status": "Live",
              "market": "South Korea",
              "language": "Korean",
              "email_opt_in": "Yes",
              "sms_opt_in": "No",
              "last_refreshed": "15 Sep 2026 23:40 UTC",
              "profile_update_status": "Updated moments ago · 4 records",
              "profile_record_1_time": "23:40",
              "profile_record_1_activity": "Product-category update",
              "profile_record_1_detail": "Desk accessories · Drinkware",
              "profile_record_2_time": "23:38",
              "profile_record_2_activity": "Campaign eligibility check",
              "profile_record_2_detail": "Autumn offers · Eligible",
              "profile_record_3_time": "23:35",
              "profile_record_3_activity": "Profile refresh",
              "profile_record_3_detail": "Live profile fields updated",
              "profile_record_4_time": "23:32",
              "profile_record_4_activity": "Audience evaluation",
              "profile_record_4_detail": "Returning customers — East Asia",
              "audience": "Returning customers — East Asia",
              "segments": "Repeat customer · Home & lifestyle · Mobile shopper",
              "preferred_categories": "Desk accessories · Drinkware",
              "last_campaign": "Autumn offers",
              "open_rate": "32.4%",
              "click_rate": "6.8%",
              "activity_1": "Autumn offers",
              "activity_1_open_rate": "32.4%",
              "activity_2": "Home & Lifestyle Picks",
              "activity_2_open_rate": "28.7%",
              "activity_3": "Profile refreshed from live data",
              "activity_3_result": "Recorded"
            },
            "source": "Audience Operations\nSoo-jin Park\nLive\nmarketing_001 · sj.park@naver.com · +82 10-5837-9451\nMarket\nSouth Korea\nLanguage\nKorean\nEmail opt-in\nYes\nSMS opt-in\nNo\nLast refreshed\n15 Sep 2026 23:40 UTC\nProfile\nUpdated moments ago · 4 records\nTime\nActivity\nDetail\n1\n23:40\nProduct-category update\nDesk accessories · Drinkware\n2\n23:38\nCampaign eligibility check\nAutumn offers · Eligible\n3\n23:35\nProfile refresh\nLive profile fields updated\n4\n23:32\nAudience evaluation\nReturning customers — East Asia\nAudience membership\nAudience\nReturning customers — East Asia\nSegments\nRepeat customer · Home & lifestyle\n· Mobile shopper\nPreferred\ncategories\nDesk accessories · Drinkware\nLast campaign\nAutumn offers\nOpen rate\n32.4%\nClick rate\n6.8%\nActivity\nAutumn offers\n32.4% open rate\nHome & Lifestyle Picks\n28.7% open rate\nProfile refreshed from live data\nRecorded"
          }
        ],
        "uncertainties": []
      }
    }
  ]
}
```
