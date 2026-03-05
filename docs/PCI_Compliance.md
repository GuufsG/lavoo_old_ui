
# PCI-DSS Compliance Documentation

## Compliance Level: SAQ A

### What We Store
✅ Stripe Customer ID (cus_XXX)
✅ Stripe Payment Method ID (pm_XXX)
✅ Card Last 4 Digits
✅ Card Brand (Visa, Mastercard, etc.)
✅ Card Expiration Month/Year

### What We NEVER Store
❌ Full Card Number (PAN)
❌ CVV/CVC Security Code
❌ Magnetic Stripe Data
❌ Chip Data
❌ PIN Numbers

### Security Measures
1. All card data tokenized by Stripe
2. TLS 1.2+ for all communications
3. No card data in logs
4. No card data in error messages
5. Database encrypted at rest
6. Access controls on payment data

### Stripe PCI Certification
- Stripe is PCI Level 1 Service Provider
- Stripe SAQ D certification
- We inherit their compliance
- Annual attestation provided by Stripe

### Our Responsibilities
- Never request raw card data
- Use Stripe.js for card collection
- Validate Stripe webhook signatures
- Secure API keys in environment variables
- Regular security audits

### Audit Trail
- All card saves logged with timestamps
- User IP addresses recorded
- Failed attempts monitored
- Suspicious activity alerts

### Data Retention
- Card tokens: Retained while user is active
- Inactive accounts: Tokens deleted after 90 days
- Deleted accounts: All data purged within 30 days