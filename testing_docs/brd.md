**BUSINESS REQUIREMENTS DOCUMENT**

**C.1.5. Virtual Account \- Account Management** 

Corporate Digital Banking Platform

(Mapped with SOW in Contract: C.1.5. Virtual Account Inquiry)



	**Table of Content**

[1\. Document Control	3](#1.-document-control)

[1.1 Version History	3](#1.1-version-history)

[2\. Executive Summary	5](#2.-executive-summary)

[2.1 General Information	5](#2.1-general-information)

[2.2 Objective	5](#2.2-objective)

[3\. General Requirement	6](#heading)

[3.1 Precondition	6](#3.1-precondition)

[3.2 In-Scope:	6](#3.2-in-scope:)

[4\. Process flow and Users	9](#4.-process-flow-and-users)

[4.1 Full process flow	9](#4.1-full-process-flow)

[4.2 Process flow Description	9](#4.2-process-flow-description)

[4.3 Corporate Users	9](#4.3-corporate-users)

[5\. Business Requirements	9](#5.-business-requirements)

[5.1 Business Functions	9](#5.1-business-functions)

[5.2 Detailed Requirements \- Data Fields	12](#5.2-detailed-requirements---data-fields)

[5.2.1 Accounts List Information (BF-AS-VA-02)	12](#5.2.1-accounts-list-information-\(bf-as-va-02\))

[5.2.2 Virtual Account Detail Information (BF-AS-VA-03)	13](#heading-1)

[5.2.3 Virtual Account Transaction History View (BF-AS-VA-04)	15](#5.2.3-virtual-account-transaction-history-view-\(bf-as-va-04\))

[6\. Notifications & Alerts	19](#6.-notifications-&-alerts)

[7\. Regulatory and Compliance, Legal Requirements	19](#7.-regulatory-and-compliance,-legal-requirements)

[8\. Glossary	19](#8.-glossary)

[9\. Reference Documents	20](#9.-reference-documents)

# **1\. Document Control** {#1.-document-control}

## 1.1 Version History {#1.1-version-history}

| Version | Date | Change type | Change section | Change summary | Change reason | Author | Reviewer |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| 1.0 | 19/05/2026 | Initial Version |  |  |  | Srinivas Lenka | Kiran Reddy |
| 1.1 | 22/05/2026 | Revised draft |  | **Fresh Section:** ‘Recommended Features’ **Moved out:** Technical details  **Moved to ‘Addendum’ section:** 1\. Out-of-scope  2\. Definition of Done 3\. Reference Screenshots 4\. Business Rules & Controls **Sections modified:** 1\. Preconditions and Dependencies merged | VCB feedback | Srinivas Lenka | Kiran Reddy |
| 1.2 | 28/05/2026 | Revised draft |  | **Moved out:** 1\. Technical details  2\. Exception Handling 3\. Success Criteria 4\. Definition of Done **Sections Modified:** Business Functions & Business rules merged and simplified | VTS Internal Discussion | Srinivas Lenka | Kiran Reddy |
| 1.3 | 15/06/2026  | Revised draft |  | **Fresh Section** 1\. Business Validation 2\. Reporting Requirements 3.Transactional Lifecycle **Sections Modified**  Data Requirements | VTS Internal Discussion | Srinivas Lenka | Kiran Reddy |
| 1.4 | 03/07/2026 | Revised draft |  | Final Feedback clarifications and changes  | VCB Discussions | Srinivas Lenka | Kiran Reddy |
| 1.5 | 20/07/2026  |   Revised Draft |   | Section 3 & 5 rewritten  |    VCB   discussions  | Srinivas  Lenka | Kiran Reddy  |
| 1.6 | 29/07/2026 |   Final Draft | Cleaned up BRD | Cleaned up BRD with all changes incorporated |  | Srinivas Lenka | Kiran Reddy |

# 

# **2\. Executive Summary** {#2.-executive-summary}

## 2.1 General Information {#2.1-general-information}

* **Product/Service:** C.1.4 Account Service \- Virtual Account (within the Corporate DigiBank Platform)  
* **Product Category:** Mass Market  
* **Brief Description:** Enables corporate users to view Virtual accounts information digitally. Core features include balance inquiry, statement view, and transaction history access   
* **Targeted Users:** Corporate users who access the channel, including specific roles like Maker, Checker, Approver, and Viewer.  
* **Targeted Customers:** Existing corporate clients who currently utilize the Corporate Digi bank platform.  
* **Applicable channels: Web** and Mobile.

## 2.2 Objective {#2.2-objective}

This Business Requirements Document (BRD) defines the end-to-end functional, business, and operational requirements for the Virtual Account (reference: C.1.4. Account Service \- Virtual Account) within the Corporate DigiBank Platform.This documentation aims at:

* Discuss the business requirements.  
* Establish the initial common understanding of virtual account modules.  
* This document will be used for further analysis and design activities.


To align the terminology with Decision No. 770/QD-VCB.CSSPBB dated 23/05/2022 on the regulations for the receivables management service using virtual account, some terms have been updated as follows:

**Structure of Virtual Account Number**:  
**Virtual Account Number \= Bank Code (Root VA) \+ Customer Code**  
**Bank Code (Root VA) \= VA Product Code \+ Customer Root VA** (Customer Root VA is associated with each CIF and can be defined based on the customer's preference, provided that it meets the character length requirements specified by the bank).

**Note:** During the design phase, the BRD may continue to be updated based on technical feasibility assessments and user experience improvements. Such updates should not be treated as Change Requests, but rather as proposed refinements to the solution. The sample or screen layouts/ mockups (if any) provided in the document are solely for depicting the functionality of the application, for reference only and not for fields/ functional confirmation. The actual look and feel of the application may vary according to the branding, theme and data field confirmations specific to the implementation.

#  {#heading}

# **3\. General Requirement**

## 3.1 Precondition  {#3.1-precondition}

**Corporate users:** 

* Corporate Users have successfully completed onboarding and are provisioned with appropriate entitlements and are authorized to inquire / view Virtual account information.    
* Corporate Users must already possess a current account which has a Virtual account linked.   
* Local banking and data privacy regulations are adhered 

## 3.2 In-Scope: {#3.2-in-scope:}

3.2.1 Common Functionalities (across the Account Management module)

* Account Services Interface Hierarchy:  
> > > * Aggregated Summary of Accounts \>\> Account List information \>\> Account Details Information \>\> Transaction History  
    
* Aggregated Summary of Accounts:  
* A real-time aggregation of information of all accounts that the User is entitled to view. This should give generic information of the Entity/Entities and a consolidated inflow of cash position view  
* The scope should cover Single CIF scenarios and group of CIF scenarios.  
    
* Account List information:  
* Real-time consolidation of all linked corporate Virtual Account information under a single view    
* Ability to download/export list of selected accounts   
* Corporate user should be able to see Accounts with ‘**Closed**’ status (upto last 12 months)  
    
* Account Details information:  
* View of account specific point-in-time granular information based on Corporate User’s authorization  
    
* Transaction History  
* View of transaction history for any account based on Corporate User’s authorization with preset period (7days by default, 14 days, last month, last 3 months) and customizable date range selection  
* Transaction history is limited to a maximum of 3-5 years from the current date for Current Account and Virtual Account.   
* The maximum inquiry period per query is 12 months  
    
* Reports & Documents:  
    
* Ability to download and export single and multiple documents related to Virtual accounts.  
* Maximum look-up data is for the past 3-5 years from the current date, with a maximum of 12 months inquiry period per query.  
* Digital Signature (wherever applicable): May incur a fee \- *More details in the fee & charges BRD*

* **3.2.1 (a) List of documents table:**

| No.  | Document Type  | Additional Information  | Format required  | Digital Signature option  |
| :---: | :---- | :---- | :---- | :---: |
| 1  | Virtual Account List | Select accounts from the account list to download/export  | 1\. PDF  2\. XLSX  | No  |
| 2  | Virtual Account Statement (Single/Multi-accounts)  | 1\. Counterparty account information \+ Sender information: account number, name (for credit transactions), sending bank \+ Beneficiary information: Virtual account number, name (for credit transactions) and Current Account number  2\. Transaction details (Date & Timestamp: dd/mm/yy hh:mm:ss, Transaction reference number, amount and currency, fee breakdown \+ VAT)   | 1\. PDF  2\. XLSX  3\. CSV   **Note:**  We have only three types of Formats in Virtual account  | Yes  (Only for PDF format)  |
| 3  | Account information confirmation  (Single/multi-accounts)  | 1\. Applicable for Same day request only  2\. Only Applicable for Accounts in Active status  | PDF  | Yes  (Only for PDF format)  |
| 4  | Transaction History  | 1\. Upto 12 months per query.   2\. Transaction history is limited to a maximum of past 3-5 years from the current date for Current Account and Virtual Account  3\. Allow users to download transaction history in   ​Excel/PDF format.  ​• By Virtual Account / By Root VA/Parent account | 1\. PDF  2\. XLSX  | No  |

  	

   

* Personalization:  
* Feature to mark **Favorite Accounts** at a User-profile level (upto 10 Favorites)  
* Create, update or delete **Nickname** at a User-profile level (upto 30 characters)  
    
* Display controls:  
> > > * **Column customization:**  
> > > * **Column drag & drop:** Corporate User should also be able to reorder the column positions as per the information they would want to see first (by drag & drop of column headers)   
> > > * **Column Menu:** Corporate User should be able to choose columns they want to view from the Column Menu  
> > > * The customized column view should be **saved automatically** so that the Corporate User has the **same view for future logins**, until the time the Corporate User changes the view again   
> > > * Corporate User should also have an option to **reset** the columns to default setting   
> > > * **Sort:** Corporate Users should be able to sort by ascending/descending order in each column   
> > > * **Search:** Corporate Users should also be able to perform searches to filter accounts with any of the information fields available (for example: CIF, Current Account number, Current account name, Virtual Account name, Parent VA name, Root VA, Virtual account Number, Parent VA number and Virtual Account Nick name.), the user should also be able to do Partial search with a key words entered across all Virtual account on all pages.   
> > > * **Filter:** Corporate Users should be able to filter basis information in any of the given column headers  
> > > * Quick Filter: Filters that allow the Corporate User fast access to specific data through predefined filters  
> > > * More Filter: This provides Corporate User more options to filter data as needed    
> > > * **Pagination Controls:** Corporate User should be able to change the number of rows/records shown on the Account List selecting from options of 10 (by default), 20 & 50

3.2.2 Virtual Account specific customization & Utilities

* **QR Code**: Generated/available for download or in-app sharing in a visual image format  
* The Corporate User should have an option to be redirected to the Current account screen of the selected Virtual account only for the granted users defined in the entitlement.   
* Virtual Account hierarchy structure to be shown in a separate tab  
* The Opening Balance on the statement should be ‘Zero’.

# **4\. Process flow and Users**  {#4.-process-flow-and-users}

## 4.1 Full process flow {#4.1-full-process-flow}

 Will be detailed in the Design Document

## 4.2 Process flow Description  {#4.2-process-flow-description}

 Will be detailed in the Design Document

## 4.3 Corporate Users  {#4.3-corporate-users}

* Corporate Maker   
* Corporate Checker   
* Corporate Approver  
* Corporate Viewer

# **5\. Business Requirements** {#5.-business-requirements}

## 5.1 Business Functions {#5.1-business-functions}

The following capabilities are required for the Virtual Account Enquiry feature: 

**BF-AS-VA-01: Aggregated Summary of Accounts:**

* Entity selection:   
  Scenario 1: **Single CIF** only, the corporate user \-  
* Should see the CIF they are entitled and authorized to view, along with the relevant Accounts under the CIF.  
  Scenario 2: **Multi-CIF or group of CIF are available**, the Corporate User \-   
* Should have an option to view Virtual account lists across all entities for which the Corporate User is entitled to.  
* Should have an option to select specific entities from a list.   
* Operational Performance Metrics:   
* Corporate User should be able to see below performance metrics for the Virtual accounts that the corporate user entitled to view:  
* The User should be able to see the hierarchy view from Current account, Root VA, Parent VA, and Virtual account as per the Entitlement defined in the system.  
* Show the No, of VA linked to Parent VA, Current Account and the Total number of VA.


**BF-AS-VA-02: Accounts List Information \- Meets the requirements of PSF C.1.5.1.1.**

* Corporate Users should see a display of all linked corporate accounts in a consolidated List view.   
* Priority Order of display of Accounts:  
* Rule 1: Favorite Accounts to be displayed first   
* Rule 2: Active accounts and then other statuses   
* Rule 3: By opening date, from newest to oldest.


* Account List:   
* Corporate User should be able to download and export the entire Account list, or a selection from the Accounts List.

**BF-AS-VA-03: Account Details Information \- Meets the requirements of PSF C.1.5.1.2.**

* This view provides detailed information for an account selected by the Corporate User  
* Recent transactions: Last 10 transactions to be shown. User can use the ‘View more’ option to go to the Transaction history page to view   
* Today’s Inflow: Total inflow of funds into the Virtual account today  
* Month-to-date credits: Total inflow of funds into the Virtual account from the first day of the current month up to the current date/time


**BF-AS-VA-04: Virtual Account Transaction History** \- **Meets the requirements of PSF C.1.5.1.3.**

* Corporate Users should be able to view/download/export Transaction history for a selected date range.  
* Please refer to Section **3.2.1** for Format and additional information

**BF-AS-VA-05: Statement Generation \- Meets the requirements of PSF C.1.5.1.4.**

* The Opening Balance on the statement should be ‘Zero’.  
* Corporate User should be able to download and export account statements (with/without digital signature) with customized periods:  
> > > * Select one or multiple accounts  
> > > * Select date range (from-to)  
* Please refer to Section **3.2.1** for Format and additional information   
* The Corporate User should also be able to generate statements with large record volumes (Currently, there is a limitation of 10,000 records per statement).

**BF-AS-VA-06: Account Confirmation \- Meets the requirements of PSF C.1.5.1.5.**

* Generate Account confirmation (with/without digital signature) for one or more accounts.   
> > > * Account Information/Existence Confirmation   
> > > * Request should be placed only for the same day only  
> > > * Applicable to Active accounts only  
* Please refer to Section **3.2.1** for Format and additional information


**BF-AS-VA-07: Personalization, Customization & Utilities \- Meets the requirements of PSF C.1.5.1.6. and C.1.5.1.7. and C.1.5.1.8.**  
 

* Mark Favorite Accounts at a Corporate User level: A personalization utility that lets Corporate Users prioritize Accounts, pinning those preferred accounts to the top of their Account list view   
* The Corporate User can set any account as Favorite, and these will be shown on top as order of priority   
* The maximum recommended number of Favorites that a Corporate User can set is 10   
    
* Create, update or delete Nickname: A user-profile level setting that enables Corporate Users to map custom titles to bank accounts for faster recognition during daily reconciliation    
* The Corporate User should be able to create, update or delete Nicknames for Accounts. Recommended length of 30 characters.   
    
* Generate QR Code: A self-service financial utility that generates a scannable payment graphic, available for download or in-app sharing in a visual image format   
* Corporate User should be able to generate a QR code with the Account information of the selected account   
* Corporate User should also have an option to enter an amount and Payment description manually and generate a QR code    
* Corporate Users should be able to download the QR code and share the QR code in-app.   
    
* Quick Filter: Should be available to filter data by each column by Root VA in Virtual accounts


* More Filters: Should allow Corporate User to filter Virtual accounts by ​​CIF​, ​​Current account number​, ​​Current account name, Root VA, ​​Virtual account number​, ​​​Virtual Account name,    
         Account nick name​, ​Partial search applies. If no results are found, display notification.  
    
* Dropdown list: Display a dropdown list of Virtual accounts, Root VA, Parent VA number and VA number authorized for inquiry access for user selection.


## **5.2 Detailed Requirements \- Data Fields** {#5.2-detailed-requirements---data-fields}

The following tables define all specific data fields required for the Virtual Account Inquiry features (Account List View, Account Details View, and Transaction History). Each entry specifies the field name, data type, mandatory status (M/C/O), and specific business requirements or handling scenarios for implementation.  
Permissions for Virtual Accounts are managed according to the following hierarchy:   
By Current Account, by Root VA, by Parent VA number VA number

### 

### **5.2.1 Accounts List Information (BF-AS-VA-02)** {#5.2.1-accounts-list-information-(bf-as-va-02)}

| \#\# | Field Name | Data Type | Display/ Input (D/I) | Mandatory / Conditional / Optional (M/C/O)  | Processing Description / Business Requirement / Scenario / Handling  |
| :---- | :---- | :---- | :---- | :---- | :---- |
|  | **Account List** |  |  |  |  |
| 1 | Current Account Number | Numeric | D | M | The Current Account the VA is mapped under |
| 2 | Current Account Name | Text | D | M | Display the current account name |
| 3 | Bank Code/Root VA | Alphanumeric | D | M | Display bank code of the virtual account |
| 4 | Parent Virtual Account Number | Alphanumeric | D | M | Display parent virtual account number (one level above child virtual account)  |
| 5 | Virtual Account Type | Text (String) | D | M | Display virtual account type \- Customer VA \- Add on \- Self VA \- Add on \- On Behalf of VA |
| 6 | Virtual Account Name | Text | D | M | Display the Virtual Account Name |
| 7 | Virtual Account Number | Alphanumeric | D | M | Display virtual account number |
| 8 | Virtual Account Nick Name | Text (String) | D | M | Display the virtual account nick name defined by the  user |
| 9 | Currency | Text | D | M | Display currency of the settlement account |
| 10 | Status | Text | D | M | Active / Closed |

**5.2.2 Virtual Account Detail Information (BF-AS-VA-03)**

| \#\# | Field Name | Data Type | Display / Input (D/I) | Mandatory / Conditional / Optional (M/C/O)  | Processing Description / Business Requirement / Scenario / Handling  |
| :---- | :---- | :---- | :---- | :---- | :---- |
| I | **Organisation Information** |  |  |  |  |
| 1 | Business Registration Number | NUmeric | D | M | The Business Identifier that is unique to the Entity/Corporate. A.k.a Investment License/ Decision No.   |
| 2 | Organizational Name | Text | D | M | Name of the Corporate Customer  |
| 3 | CIF Number    | Numeric | D | M | Customer identification File number that is created when the Entity/Corporate Customer is successfully onboarded. This is a Unique identifier for the organization   |
| 4 | Address | Text (String) | D | M | Address of the Entity/Corporate Customer that has been registered with VCB   |
|  | **Account Information** |  |  |  |  |
| 1\. |  Managing Branch  | Text  | D | M | The VCB Branch where the account was opened |
| 2 | Virtual Account Number | Alphanumeric  | D | M  | A unique, system-generated identifier assigned to\` a virtual account.  |
| 3 | Current Account Number | Numeric  | D | M | The underlying Physical Bank Account Number while virtual accounts maintain logical balances for reporting, actual money always resides in and settles through this linked current account. .  |
| 4 | Account Type | Text (String)  | D | M  | Category of Virtual account defined by the Bank |
| 5 | Root VA | Alphanumeric | D | M | The top-level account of a multi-tiered virtual account hierarchy tree. All child and sub-child virtual accounts eventually roll up their logical balances and transaction reporting into the Root VA before settling into the Current Account.  |
| 6 | Parent Virtual Account Number | Alphanumeric | D | M  | The unique identifier of the immediate higher-level virtual account directly above a virtual account in a hierarchical structure.  |
| 7 | Virtual Account Name | Text | D | M  | The formal account name associated with the virtual account. . |
| 8 | Virtual Account Nick name | Text | D | M | An informal, user-friendly name assigned internally by corporate users for easy identification. |
| 9 | Currency | Text  | D | M | Currency of the Virtual Account |
| 10 | Virtual Account Opening Date | Date | D | M | The exact date on which the virtual account was provisioned and created in the Virtual Account Management system  |
| 11 | Virtual Account Status | String | D | M | Active/Closed. |
|  | **Account Balance Information** |  |  |  |  |
| 1 | Today’s Inflow | Numeric | D | M | Total inflow of funds into the Virtual account today |
| 2 | Month-to-date credits | Numeric | D | M | Total inflow of funds into the Virtual account from the first day of the current month up to the current date/time |

### **5.2.3 Virtual Account Transaction History View (BF-AS-VA-04)** {#5.2.3-virtual-account-transaction-history-view-(bf-as-va-04)}

Specific Business Requirement Details — Data Fields

**Transaction History View \- Field Description**

| \#\# | Field Name | Data Type | Display / Input (D/I) | Mandatory / Conditional / Optional (M/C/O)  | Processing Description / Business Requirement / Scenario / Handling  |
| :---: | ----- | ----- | ----- | ----- | ----- |
|  | General Information |  |  |  |  |
| 1 | Opening Balance | Numeric | D | M | The Opening Balance on the statement should be ‘Zero’. |
| 2 | Closing Balance | Numeric | D | M | The final net amount of funds remaining in an account at a given reporting period after all period activity has been processed. |
|  | **Transaction History Details** |  |  |  |  |
| 1 | Transaction Reference Number  | Numeric | D | M | A unique identifier generated for every individual transaction. |
| 2 | Transaction Date | Date | D | M | The calendar date on which the transaction was posted to the virtual account ledger (or the value date when the funds officially become available).  |
| 3 | TellerID \-Sequence | Numeric | D | M | TellerID and Sequence of channel posting entry of transactions will be displayed. Tellerid: 4 characters Sequence : 5 characters For example:  0004-01234 In case if TellerID data from DB has less than 4 characters, then those to be padded with zeroes to the left as shown in the example. In case if Sequence data from DB has less than 5 characters, then those to be padded with zeroes to the left as shown in the example. |
| 4 | Branch – Host Date – PC Time |  | D | M | Branch Code, Host Date & PC time of the channel posting entry of transactions will be displayed. Branch: 5 Characters. Host date: 8 Characters will be displayed as DD/MM/YYYY. PC time: 6 Characters and to displayed in HH:MM:SS. Example: 06800 – 16/04/2024 – 11:34:38. In case if Branch data from DB has less than 5 characters, then those to be padded with zeroes to the left as shown in the example. |
| 5 | Value Date-Time | DD/MM/:YYYY \- HH:MM:SS  | D | M | The precise timestamp (hours, minutes, seconds) indicating when the transaction hit the virtual account |
| 6 | Counterparty Account Number | Numeric | D | M | The account number belonging to the second party in the transaction—either the sender (making a credit payment) or the recipient (beneficiary) of a disbursement.  |
| 7 | Counterparty Account Name | Text (String) | D | M | The registered legal name of the sender or receiver involved in the payment. |
| 8 | Corresponde nt Bank | Text (String) | D | M | The intermediary financial institution used to facilitate cross-border or interbank settlement when a direct clearing path between the originating bank and the corporate bank does not exist.  |
| 9 | Virtual Account Number  | Alphanumeric | D | M | A unique, system-generated identifier assigned to a virtual account |
| 10 | VA Nick Name | Text | D | M | An informal, user-friendly name assigned internally by corporate users for easy identification. |
| 11 | VA Type | Text (String) | D | M | Display virtual account type \- Customer VA \- Add on \- Self VA \- Add on \- On Behalf of VA |
| 12 | VA Name  | Text | D | M  | The formal account name associated with the virtual account. . |
| 13 | Root VA | Alphanumeric | D | M | The top-level account of a multi-tiered virtual account hierarchy tree. All child and sub-child virtual accounts eventually roll up their logical balances and transaction reporting into the Root VA before settling into the Current Account.  |
| 14 | Parent VA | Alphanumeric | D | M | The unique identifier of the immediate higher-level virtual account directly above a virtual account in a hierarchical structure.  |
| 15 | Current Account Number  | Numeric | D | M | The underlying Physical Bank Account Number while virtual accounts maintain logical balances for reporting, actual money always resides in and settles through this linked current account. . |
| 16 | Debit Amount | Numeric | D | M | The specific monetary value added (debited) to the virtual account |
| 17 | Credit Amount | Numeric | D | M | The specific monetary value added (credited) to the virtual account ledger for a single incoming payment or internal ledger transfer.  |
| 18 | Running Balance | Numeric (Decimal) | D | M | The current balance of the virtual account (accumulated balance calculated from the statement start date to the statement end date- selected by the Corporate User) |
| 19 | Currency | Text (String) | D | M | Currency of the Virtual Account  |
| 20 | Transaction Description | Text (String) | D | M | Additional narrative provided by the remitter or bank |

# **6\. Notifications & Alerts** {#6.-notifications-&-alerts}

| Event | Recipient | Channel | Content |
| ----- | ----- | ----- | ----- |
| Credit Notification  | Corporate user  | Mobile/Email/Web | Notification to be pushed in real time with Credit Transaction.    |

# **7\. Regulatory and Compliance, Legal Requirements** {#7.-regulatory-and-compliance,-legal-requirements}

* The Virtual Account services module is a UI that aggregates information from the Corporate Banking System and works with the Entitlements that are configured on the Corporate Banking System for the Corporate accounts.  
* Authorized Access: User should have authorization to access and view the module.  
* Personal data must be handled in accordance with Vietnam's Law on Cybersecurity and the bank's data privacy policy.  
* Audit Trail must track all access and downloads with user identity and timestamp, retained for the regulatory minimum period.

# **8\. Glossary** {#8.-glossary}

| Term / Acronym | Definition |
| :---- | :---- |
| Virtual Account | A virtual sub-account linked to a real/physical settlement account, used to segregate and track funds without holding an independent balance. Transactions are routed through the linked settlement account. |
| Corporate Digital Banking System | The digital banking platform provided by VCB to corporate customers, enabling account management, payments, and financial operations through online channels. |
| Root VA | The top-level Virtual Account in the account hierarchy, from which child or sub-virtual accounts may be structured. Entitlement and visibility rules may be applied starting from this level. |
| Current Account | The physical bank account (current account) to which one or more Virtual Accounts are linked. All VA transactions are ultimately settled through this account. |
| Parent Virtual account Account | The identifier of the parent Virtual Account, one level above child Virtual Accounts in the account hierarchy. |
| Account Information Confirmation | A chronological record of all financial transactions (credits and debits) associated with a Virtual Account within a selected inquiry period. |
| Transaction Reference Number | A formal summary of transactions for one or more Virtual Accounts over a defined period, available for download or print in PDF format. |
| Statement | A SWIFT standard message format used to deliver end-of-day bank account statements, including transaction details and closing balances. Used for reconciliation with ERP or treasury systems. |
| Entity | An individual (corporate staff member) with credentials to access the Corporate Digital Banking system. User access is governed by assigned roles and entitlements. |

# **9\. Reference Documents** {#9.-reference-documents}

VCB BRD standards, SBV regulations

**10\. Additional Feature Functionalities** 

| S.No | Feature Name & Description | Functional capability and Description | Business Value | Feasibility and Dependencies | How it works  | Outcome |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| **1** | **Virtual Account "Smart-Search"** A prominent, global search bar at the top of the interface that allows unified searching across all accounts. | Global multi-field indexing, fuzzy-matching search capability, and instant cross-account query results without column-based filters. | Saves significant administrative time spent cross-referencing files to locate specific transactions. | **Feasibility:** Medium complexity. **Dependencies:** Requires global indexing of key transaction fields (e.g., invoice numbers, reference IDs) across all sub-accounts. | 1\. **Input Query:** The user enters a unique reference identifier (e.g., "Invoice \#12345") into the top search bar. 2\. **Cross-Account Scan:** The system instantly searches across all transaction history databases. 3\. **Display Results:** The system displays the matching record, showing exactly which Virtual Account received the funds. | Operations teams locate misplaced or unverified incoming funds in seconds without needing to know which specific sub-account received from them. |

## 

\---------------------------------------- End of Document \----------------------------------------  