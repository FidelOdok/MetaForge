"""GraphQL query strings for the Nexar/Octopart API (MET-176)."""

from __future__ import annotations

SEARCH_PARTS_QUERY = """
query SearchParts($query: String!, $limit: Int!) {
  supSearch(q: $query, limit: $limit) {
    results {
      part {
        mpn
        manufacturer {
          name
        }
        shortDescription
        bestDatasheet {
          url
        }
        sellers {
          company {
            name
          }
          offers {
            inventoryLevel
            moq
            prices {
              quantity
              price
              currency
            }
            factoryLeadDays
          }
        }
        specs {
          attribute {
            name
          }
          displayValue
        }
        category {
          name
        }
      }
    }
  }
}
"""

PART_DETAILS_QUERY = """
query PartDetails($mpn: String!) {
  supSearchMpn(q: $mpn) {
    results {
      part {
        mpn
        manufacturer {
          name
        }
        shortDescription
        bestDatasheet {
          url
        }
        sellers {
          company {
            name
          }
          offers {
            inventoryLevel
            moq
            prices {
              quantity
              price
              currency
            }
            factoryLeadDays
            factoryPackQuantity
          }
        }
        specs {
          attribute {
            name
          }
          displayValue
        }
        category {
          name
        }
        descriptions {
          text
        }
      }
    }
  }
}
"""
